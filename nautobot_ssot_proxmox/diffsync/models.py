from typing import Optional, List
from nautobot_ssot.contrib import NautobotModel
from nautobot.virtualization.models import Cluster as NBCluster
from nautobot.virtualization.models import VirtualMachine as NBVM
from nautobot.virtualization.models import VMInterface as NBVMInterface
from nautobot.ipam.models import IPAddress as NBIPAddress


def _get_proxmox_vm(name: str) -> NBVM:
    """Get a VM by name, preferring the one with a proxmox_vmid custom field."""
    qs = NBVM.objects.filter(
        name=name,
    ).exclude(
        _custom_field_data__proxmox_vmid=""
    ).filter(
        _custom_field_data__proxmox_vmid__isnull=False
    )
    if qs.count() == 1:
        return qs.first()
    # Fallback: just get any VM with that name and a proxmox_vmid
    return qs.first()


def _sync_ip_addresses(iface: NBVMInterface, ip_list: List[str], vm: NBVM, parent_prefix_id: str):
    from nautobot.extras.models import Status
    from nautobot.ipam.models import IPAddress, IPAddressToInterface, Prefix
    import netaddr

    active_status = Status.objects.get(name="Active")
    try:
        parent_prefix = Prefix.objects.get(id=parent_prefix_id)
    except Prefix.DoesNotExist:
        raise RuntimeError(f"Parent prefix with ID '{parent_prefix_id}' not found. Check PARENT_PREFIX_ID in config.")

    desired_addresses = set(ip_list)

    # Remove IP assignments no longer present
    current_assignments = IPAddressToInterface.objects.filter(vm_interface=iface)
    for assignment in current_assignments:
        if str(assignment.ip_address.address) not in desired_addresses:
            assignment.delete()

    # Create or update desired IPs
    for address in ip_list:
        net = netaddr.IPNetwork(address)
        ip_obj, _ = IPAddress.objects.get_or_create(
            host=str(net.ip),
            mask_length=net.prefixlen,
            parent=parent_prefix,
            defaults={"status": active_status},
        )
        ip_obj.status = active_status
        ip_obj.save()

        IPAddressToInterface.objects.get_or_create(
            ip_address=ip_obj,
            vm_interface=iface,
        )

        if not vm.primary_ip4:
            vm.primary_ip4 = ip_obj
            vm.save()


class ClusterModel(NautobotModel):
    _model = NBCluster
    _modelname = "cluster"
    _identifiers = ("name",)
    _attributes = ("cluster_type__name",)

    name: str
    cluster_type__name: Optional[str] = None

    @classmethod
    def create(cls, adapter, ids, attrs):
        from nautobot.virtualization.models import ClusterType
        cluster_type = ClusterType.objects.get(name=attrs["cluster_type__name"])
        NBCluster.objects.get_or_create(
            name=ids["name"],
            defaults={"cluster_type": cluster_type},
        )
        obj = cls(**ids, **attrs)
        adapter.add(obj)
        return obj

    def update(self, attrs):
        from nautobot.virtualization.models import ClusterType
        cluster = NBCluster.objects.get(name=self.name)
        if "cluster_type__name" in attrs:
            cluster.cluster_type = ClusterType.objects.get(name=attrs["cluster_type__name"])
            cluster.save()
        self.__dict__.update(attrs)
        return self


class VirtualMachineModel(NautobotModel):
    _model = NBVM
    _modelname = "virtualmachine"
    _identifiers = ("custom_fields__proxmox_vmid",)
    _attributes = (
        "name",
        "vcpus",
        "memory",
        "status__name",
        "cluster__name",
        "custom_fields__proxmox_node",
        "custom_fields__proxmox_type",
    )

    custom_fields__proxmox_vmid: str
    name: str
    vcpus: int
    memory: int
    status__name: str
    cluster__name: str
    custom_fields__proxmox_node: Optional[str] = None
    custom_fields__proxmox_type: Optional[str] = None

    @classmethod
    def create(cls, adapter, ids, attrs):
        from nautobot.extras.models import Status, Role
        from nautobot.virtualization.models import Cluster
        from django.conf import settings

        cfg = settings.PLUGINS_CONFIG.get("nautobot_ssot_proxmox", {})
        cluster = Cluster.objects.get(name=attrs["cluster__name"])
        status = Status.objects.get(name=attrs["status__name"])
        vmid = ids["custom_fields__proxmox_vmid"]
        vmtype = attrs.get("custom_fields__proxmox_type")

        # Pick role based on type
        if vmtype == "lxc":
            role_id = cfg.get("LXC_ROLE_ID")
        else:
            role_id = cfg.get("VM_ROLE_ID")
        role = Role.objects.get(id=role_id) if role_id else None

        vm = NBVM.objects.filter(
            _custom_field_data__proxmox_vmid=vmid
        ).first()

        if vm:
            vm.name = attrs["name"]
            vm.vcpus = attrs.get("vcpus", 0)
            vm.memory = attrs.get("memory", 0)
            vm.status = status
            vm.cluster = cluster
            if role:
                vm.role = role
        else:
            vm = NBVM(
                name=attrs["name"],
                vcpus=attrs.get("vcpus", 0),
                memory=attrs.get("memory", 0),
                status=status,
                cluster=cluster,
                role=role,
            )

        vm._custom_field_data["proxmox_vmid"] = vmid
        vm._custom_field_data["proxmox_node"] = attrs.get("custom_fields__proxmox_node")
        vm._custom_field_data["proxmox_type"] = attrs.get("custom_fields__proxmox_type")
        vm.save()

        obj = cls(**ids, **attrs)
        adapter.add(obj)
        return obj

    def update(self, attrs):
        from nautobot.extras.models import Status, Role
        from nautobot.virtualization.models import Cluster
        from django.conf import settings

        cfg = settings.PLUGINS_CONFIG.get("nautobot_ssot_proxmox", {})
        vm = NBVM.objects.get(
            _custom_field_data__proxmox_vmid=self.custom_fields__proxmox_vmid
        )
        if "name" in attrs:
            vm.name = attrs["name"]
        if "vcpus" in attrs:
            vm.vcpus = attrs["vcpus"]
        if "memory" in attrs:
            vm.memory = attrs["memory"]
        if "status__name" in attrs:
            vm.status = Status.objects.get(name=attrs["status__name"])
        if "cluster__name" in attrs:
            vm.cluster = Cluster.objects.get(name=attrs["cluster__name"])
        if "custom_fields__proxmox_node" in attrs:
            vm._custom_field_data["proxmox_node"] = attrs["custom_fields__proxmox_node"]
        if "custom_fields__proxmox_type" in attrs:
            vm._custom_field_data["proxmox_type"] = attrs["custom_fields__proxmox_type"]

        # Always sync role based on current type
        vmtype = vm._custom_field_data.get("proxmox_type")
        if vmtype == "lxc":
            role_id = cfg.get("LXC_ROLE_ID")
        else:
            role_id = cfg.get("VM_ROLE_ID")
        if role_id:
            vm.role = Role.objects.get(id=role_id)

        vm.save()
        self.__dict__.update(attrs)
        return self

class VMInterfaceModel(NautobotModel):
    _model = NBVMInterface
    _modelname = "vminterface"
    _identifiers = ("virtual_machine__name", "name")
    _attributes = ("mac_address", "ip_addresses")

    virtual_machine__name: str
    name: str
    mac_address: Optional[str] = None
    ip_addresses: Optional[List[str]] = None

    @classmethod
    def create(cls, adapter, ids, attrs):
        from django.conf import settings
        cfg = settings.PLUGINS_CONFIG.get("nautobot_ssot_proxmox", {})
        parent_prefix_id = cfg.get("PARENT_PREFIX_ID")

        vm = _get_proxmox_vm(ids["virtual_machine__name"])
        if not vm:
            raise ValueError(f"VM '{ids['virtual_machine__name']}' not found")

        from nautobot.extras.models import Status
        active_status = Status.objects.get(name="Active")
        iface, _ = NBVMInterface.objects.get_or_create(
            virtual_machine=vm,
            name=ids["name"],
            defaults={
                "mac_address": attrs.get("mac_address"),
                "status": active_status,
            },
        )
        if attrs.get("mac_address"):
            iface.mac_address = attrs["mac_address"]
            iface.save()
        _sync_ip_addresses(iface, attrs.get("ip_addresses") or [], vm, parent_prefix_id)

        obj = cls(**ids, **attrs)
        adapter.add(obj)
        return obj

    def update(self, attrs):
        from django.conf import settings
        cfg = settings.PLUGINS_CONFIG.get("nautobot_ssot_proxmox", {})
        parent_prefix_id = cfg.get("PARENT_PREFIX_ID")

        vm = _get_proxmox_vm(self.virtual_machine__name)
        if not vm:
            raise ValueError(f"VM '{self.virtual_machine__name}' not found")

        iface = NBVMInterface.objects.get(
            virtual_machine=vm,
            name=self.name,
        )
        if "mac_address" in attrs and attrs["mac_address"]:
            iface.mac_address = attrs["mac_address"]
            iface.save()
        if "ip_addresses" in attrs:
            _sync_ip_addresses(iface, attrs["ip_addresses"] or [], vm, parent_prefix_id)
        self.__dict__.update(attrs)
        return self
