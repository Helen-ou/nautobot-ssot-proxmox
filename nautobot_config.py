PLUGINS = ["nautobot_ssot", "nautobot_ssot_proxmox"]

# Plugins configuration settings. These settings are used by various plugins that the user may have installed.
# Each key in the dictionary is the name of an installed plugin and its value is a dictionary of settings.
#
PLUGINS_CONFIG = {
        "nautobot_ssot_proxmox": {
        "PROXMOX_URL": "192.168.252.13",
        "PROXMOX_USER": "nautobot-ssot@pve",
        "PROXMOX_TOKEN_NAME": "nautobot-proxmox",
        "PROXMOX_TOKEN_VALUE": "",
        # Change to your proxmox token. Be aware that it should have the same ACL rights as your user.
        "VERIFY_SSL": False,
        "CLUSTER_NAME": "pve01",
        "CLUSTER_TYPE_NAME": "Proxmox VE",
        "DELETE_MISSING": False,
        "ALLOWED_SUBNETS": ["192.168.250.0/24", "192.168.216.0/24"],
    	"PARENT_PREFIX_ID": "f469ff84-5acc-4890-9a96-842228354a1d",
        # Get them directly from the nautobot GUI tbh. You could automate in the code their retrieve, but eh.
        # Got them for example from : IPAM > Prefixes > 192.168.0.0/16 > Advanced
        "VM_ROLE_ID": "00c588b8-0827-4803-983e-b3a5024abbc4",
        "LXC_ROLE_ID": "8cd75551-2fe8-4980-b385-41b41d48c1cf",
    },
}

