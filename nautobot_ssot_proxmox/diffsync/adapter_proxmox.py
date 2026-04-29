from typing import Optional, List
from ipaddress import ip_address, ip_network
from diffsync import Adapter
from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException
from requests.exceptions import RequestException

from ..const import (
    CFG_PROXMOX_URL, CFG_PROXMOX_USER, CFG_PROXMOX_TOKEN_NAME,
    CFG_PROXMOX_TOKEN_VALUE, CFG_VERIFY_SSL, CFG_CLUSTER_NAME,
    CFG_CLUSTER_TYPE_NAME, CFG_ALLOWED_SUBNETS,
)
from .models import ClusterModel, VirtualMachineModel, VMInterfaceModel

LOOPBACK_MAC = "00:00:00:00:00:00"


class ProxmoxAdapter(Adapter):
    top_level = ["cluster", "virtualmachine", "vminterface"]

    cluster = ClusterModel
    virtualmachine = VirtualMachineModel
    vminterface = VMInterfaceModel

    def __init__(self, *args, config: dict, job=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job
        self.config = config
        verify_ssl = bool(self.config.get(CFG_VERIFY_SSL, True))

        # Parse allowed subnets from config
        raw_subnets = self.config.get(CFG_ALLOWED_SUBNETS, [])
        self.allowed_subnets = []
        for s in raw_subnets:
            try:
                self.allowed_subnets.append(ip_network(s, strict=False))
            except ValueError:
                if self.job:
                    self.job.logger.warning(f"Invalid subnet in ALLOWED_SUBNETS: {s}")

        try:
            self.proxmox = ProxmoxAPI(
                host=self.config[CFG_PROXMOX_URL],
                user=self.config[CFG_PROXMOX_USER],
                token_name=self.config[CFG_PROXMOX_TOKEN_NAME],
                token_value=self.config[CFG_PROXMOX_TOKEN_VALUE],
                verify_ssl=verify_ssl,
            )
        except RequestException as err:
            raise RuntimeError(f"Failed to connect to Proxmox API: {err}") from err

    def _ip_is_allowed(self, ip_str: str) -> bool:
        """Check if an IP address falls within any of the allowed subnets."""
        if not self.allowed_subnets:
            return True  # No filter configured, allow all
        try:
            addr = ip_address(ip_str)
            return any(addr in subnet for subnet in self.allowed_subnets)
        except ValueError:
            return False

    @staticmethod
    def _status_to_nb(status: Optional[str]) -> str:
        return "Active" if status == "running" else "Offline"

    def _get_lxc_interfaces(self, node: str, vmid: str) -> List[tuple]:
        """
        Fetch interfaces for an LXC container.
        - For running LXCs: uses the live /interfaces API.
        - For stopped LXCs: falls back to parsing /config net* entries.
        Skips loopback MAC and filters by allowed subnets.
        """
        results = []

        # Try live interfaces first (works for running LXCs)
        try:
            ifaces = self.proxmox.nodes(node).lxc(vmid).interfaces.get() or []
            for iface in ifaces:
                mac = iface.get("hwaddr", "")
                if mac == LOOPBACK_MAC:
                    continue
                inet = iface.get("inet")
                if not inet:
                    continue
                ip_only = inet.split("/")[0]
                if not self._ip_is_allowed(ip_only):
                    continue
                results.append((iface.get("name"), mac, inet))
        except Exception:
            ifaces = []

        # If live API returned results, we're done
        if results:
            return results

        # Fall back to config for stopped LXCs
        try:
            config = self.proxmox.nodes(node).lxc(vmid).config.get()
            # Config has keys like net0, net1, net2...
            for key, value in config.items():
                if not key.startswith("net"):
                    continue
                # value is like: "name=eth0,bridge=vmbr0,ip=192.168.250.119/24,hwaddr=BC:24:11:BD:9E:0C"
                parts = dict(p.split("=", 1) for p in value.split(",") if "=" in p)
                mac = parts.get("hwaddr", "")
                if not mac or mac == LOOPBACK_MAC:
                    continue
                inet = parts.get("ip")
                if not inet or inet == "dhcp":
                    continue
                ip_only = inet.split("/")[0]
                if not self._ip_is_allowed(ip_only):
                    continue
                iface_name = parts.get("name", key)
                results.append((iface_name, mac.upper(), inet))
        except Exception as err:
            if self.job:
                self.job.logger.warning(
                    f"Could not fetch LXC {vmid} config on {node}: {err}"
                )

        return results

    def _get_qemu_interfaces(self, node: str, vmid: str) -> List[tuple]:
        """
        Fetch interfaces for a QEMU VM via guest agent.
        Returns list of (iface_name, mac, ip_with_prefix) tuples.
        Skips loopback MACs, non-IPv4, and filters by allowed subnets.
        Returns [] silently if guest agent is unavailable.
        """
        results = []
        try:
            data = self.proxmox.nodes(node).qemu(vmid).agent("network-get-interfaces").get()
            for iface in data.get("result", []):
                mac = iface.get("hardware-address", "")
                if mac == LOOPBACK_MAC:
                    continue
                name = iface.get("name")
                for addr in iface.get("ip-addresses", []):
                    if addr.get("ip-address-type") != "ipv4":
                        continue
                    ip = addr.get("ip-address")
                    prefix = addr.get("prefix")
                    if not ip or prefix is None:
                        continue
                    if not self._ip_is_allowed(ip):
                        continue
                    results.append((name, mac, f"{ip}/{prefix}"))
        except Exception as err:
            if self.job:
                self.job.logger.warning(
                    f"QEMU VM {vmid} on {node}: guest agent unavailable or no IPs returned ({err})"
                )
        return results

    def load(self):
        cluster_name = self.config[CFG_CLUSTER_NAME]
        cluster_type = self.config.get(CFG_CLUSTER_TYPE_NAME, "Proxmox VE")

        # Cluster
        cluster_obj = self.cluster(name=cluster_name, cluster_type__name=cluster_type)
        self.add(cluster_obj)

        # VMs and LXCs
        try:
            items = self.proxmox.cluster.resources.get(type="vm")
        except ResourceException as err:
            raise RuntimeError(f"Proxmox API error: {err}") from err
        except RequestException as err:
            raise RuntimeError(f"Proxmox network error: {err}") from err

        for r in items:
            vmid = str(r.get("vmid"))
            if not vmid:
                continue

            name = r.get("name") or f"vm-{vmid}"
            nb_status = self._status_to_nb(r.get("status"))
            vcpus = int(r.get("maxcpu") or 0)
            mem_mb = int((r.get("maxmem") or 0) // (1024 * 1024))
            node = r.get("node")
            vmtype = r.get("type")  # 'qemu' or 'lxc'

            vm = self.virtualmachine(
                custom_fields__proxmox_vmid=vmid,
                name=name,
                vcpus=vcpus,
                memory=mem_mb,
                status__name=nb_status,
                cluster__name=cluster_name,
                custom_fields__proxmox_node=node,
                custom_fields__proxmox_type=vmtype,
            )
            self.add(vm)

            # Fetch interfaces — empty list if guest agent unavailable (QEMU)
            if vmtype == "lxc":
                ifaces = self._get_lxc_interfaces(node, vmid)
            else:
                ifaces = self._get_qemu_interfaces(node, vmid)

            for iface_name, mac, ip_cidr in ifaces:
                try:
                    iface_obj = self.vminterface(
                        virtual_machine__name=name,
                        name=iface_name,
                        mac_address=mac.upper(),
                        ip_addresses=[ip_cidr],
                    )
                    self.add(iface_obj)
                except Exception as err:
                    if self.job:
                        self.job.logger.warning(
                            f"Could not add interface {iface_name} for {name}: {err}"
                        )
