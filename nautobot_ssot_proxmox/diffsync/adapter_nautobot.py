from nautobot_ssot.contrib import NautobotAdapter
from nautobot.virtualization.models import Cluster, VirtualMachine, VMInterface
from nautobot.ipam.models import IPAddress
from django.contrib.contenttypes.models import ContentType
from .models import ClusterModel, VirtualMachineModel, VMInterfaceModel


class NautobotInventoryAdapter(NautobotAdapter):
    top_level = ("cluster", "virtualmachine", "vminterface")
    cluster = ClusterModel
    virtualmachine = VirtualMachineModel
    vminterface = VMInterfaceModel

    def load_clusters(self):
        for cluster in Cluster.objects.all():
            obj = ClusterModel(
                name=cluster.name,
                cluster_type__name=cluster.cluster_type.name if cluster.cluster_type else None,
            )
            self.add(obj)

    def load_vms(self):
        for vm in VirtualMachine.objects.all():
            cf = vm._custom_field_data
            vmid = cf.get("proxmox_vmid")
            if not vmid:
                continue
            obj = VirtualMachineModel(
                custom_fields__proxmox_vmid=vmid,
                name=vm.name,
                vcpus=vm.vcpus or 0,
                memory=vm.memory or 0,
                status__name=vm.status.name if vm.status else "Active",
                cluster__name=vm.cluster.name if vm.cluster else "",
                custom_fields__proxmox_node=cf.get("proxmox_node"),
                custom_fields__proxmox_type=cf.get("proxmox_type"),
            )
            self.add(obj)

    def load_interfaces(self):
        from nautobot.ipam.models import IPAddressToInterface

        for iface in VMInterface.objects.select_related("virtual_machine").all():
            cf = iface.virtual_machine._custom_field_data
            if not cf.get("proxmox_vmid"):
                continue

            ip_list = [
                str(assignment.ip_address.address)
                for assignment in IPAddressToInterface.objects.filter(
                    vm_interface=iface
                ).select_related("ip_address")
            ]

            obj = VMInterfaceModel(
                virtual_machine__name=iface.virtual_machine.name,
                name=iface.name,
                mac_address=str(iface.mac_address) if iface.mac_address else None,
                ip_addresses=ip_list,
            )
            self.add(obj)

    def load(self):
        self.load_clusters()
        self.load_vms()
        self.load_interfaces()
