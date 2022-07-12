#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright 2020 VMware, Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

ANSIBLE_METADATA = {
    "metadata_version": "1.0",
    "status": ["preview"],
    "supported_by": "community",
}
# TODO: Update the module documentation
DOCUMENTATION = """
module: VmwareVcenterWcpCluster
short_description: Manage VMware vSphere for Kubernetes(WCP) clusters
version_added: '2.9'
description:
  - The VMware vSphere WCP Cluster users module allows you to manage state of
    vCenter clusters enabled for Kubernetes
  - check_mode is supported
requirements:
  - VMware vCenter (version 7.0.0 at least)
  - vSphere Automation SDK for Python (version 7.0.0 at least)
extends_documentation_fragment: vmware_rest_client.documentation
options:
  state:
    description:
      - The state the K8s component should be in.
    choices:
      - present
      - absent
author:
    - Vanyo Mihaylov <mihaylovv@vmware.com>
"""
EXAMPLES = """
---
- hosts: localhost
  connection: local
  gather_facts: False

  tasks:
    - name:
      vcenter_wcp_cluster:
        hostname: "{{ hostname }}"
        username: "{{ username }}"
        password: "{{ password }}"
        validate_certs: "{{ validate_certs }}"
        cluster_name: "{{ name }}"
        datacenter_name: "{{ datacenter }}"
        dvs_name: "{{ wcp.dvs_name }}"
        cluster_size: "{{ wcp.cluster_size | default(omit) }}"
        nsxt_edge_cluster_name: "{{ wcp.edge_cluster_name }}"
        service_cidr: "{{ wcp.service_cidr }}"
        pod_cidrs: "{{ wcp.pod_cidrs }}"
        ingress_cidrs: "{{ wcp.ingress_cidrs }}"
        egress_cidrs: "{{ wcp.egress_cidrs }}"
        management_network: "{{ wcp.management_network }}"
        master_dns_servers: "{{ wcp.master_dns_servers }}"
        worker_dns_servers: "{{ wcp.worker_dns_servers | default(omit) }}"
        master_dns_search_domains: "{{ wcp.master_dns_search_domains | default(omit) }}"
        master_ntp_servers: "{{ wcp.master_ntp_servers | default(omit) }}"
        master_storage_policy: "{{ wcp.master_storage_policy }}"
        ephemeral_storage_policy: "{{ wcp.ephemeral_storage_policy| default(omit) }}"
        image_storage_policy: "{{ wcp.image_storage_policy }}"
        default_image_registry: "{{ wcp.default_image_registry | default(omit) }}"
        default_image_repository: "{{ wcp.default_image_repository | default(omit) }}"
        default_kubernetes_service_content_library: "{{ wcp.default_kubernetes_service_content_library | default(omit) }}"
        login_banner: "{{ wcp.login_banner | default(omit) }}"
        state: "{{ wcp.state }}"
"""

RETURN = """
message:
  description: Message with details for the operation
  type: str
"""

from time import sleep
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils._text import to_native
from ansible.module_utils.vmware_rest_client import VmwareRestClient
from ansible.module_utils.vmware import (
    find_dvs_by_name,
    find_dvspg_by_name,
    connect_to_api,
)
import com.vmware.vapi.std.errors_client as vapi_errors
import ipaddress

# Import vSphere Automation REST for Namespaces Management VAPI Stub definitions
try:
    # Need to import the whole module for access to Classes like
    # specs, validations, errors, enums etc.
    import com.vmware.vcenter.namespace_management_client as nmc

    HAS_LIB = True
except ImportError:
    HAS_LIB = False


class VmwareVcenterWcpCluster(VmwareRestClient):
    """
    Class to manage VMware vSphere with Kubernetes clusters
    """

    MGMT_NETWORK_ADDRESS_COUNT = 5
    WCP_ENABLE_TIMEOUT = 7200
    WCP_DISABLE_TIMEOUT = 600
    WCP_CHECK_INTERVAL = 60

    def __init__(self, module):
        # TODO: Add logout from API's
        self.vcenter = module.params["hostname"]
        self.module = module
        self.desired_state = module.params["state"]
        self.cluster_name = module.params["cluster_name"]
        self.datacenter_name = module.params["datacenter_name"]

        # Disable SSL warning if connection should not be verified
        if self.module.params["validate_certs"] is False:
            import urllib3

            urllib3.disable_warnings()

        # In recent Ansible module_utils(somewhere after 2.7) the client is authenticated and connected
        # during the class VmwareRestClient initialization
        try:
            super(VmwareVcenterWcpCluster, self).__init__(module)
        except Exception as e:
            module.fail_json(
                msg="Failed to connect to vAPI for {}: {}".format(
                    self.vcenter, to_native(e)
                )
            )

        # Initialize API bindings to Namespaces management client
        try:
            self.namespaces_management_client = nmc.StubFactory(
                self.api_client.session_svc._config
            )
        except Exception as e:
            module.fail_json(
                msg="Unable to initialise Namespaces Management API binding for {}: {}".format(
                    self.vcenter, to_native(e)
                )
            )

        # Initialize vSphere WebServices API access (SOAP not vAPI)
        try:
            self.vsphere_client = connect_to_api(module)
        except Exception as e:
            self.module.fail_json(
                msg="Unable to connect to vSphere Webservices API: {}".format(
                    to_native(e)
                )
            )

        self._namespaces_supported()
        if self.desired_state == "present":
            dvs = self._find_dvs(dvs_name=self.module.params["dvs_name"])
            self.dvs = dvs
            self.dvs_uuid = dvs.summary.uuid
            # TODO: Probably rename the parse_arguments to initialise spec
            self.parsed_args = dict()
            self._parse_arguments()

        self.cluster_id = self._get_cluster_moref()

    def process_state(self):
        """ Ensures the desired state of the managed service """
        states = {
            "present": {
                "present": self._update_cluster,
                "notready": self._wait_cluster_enable,
                "absent": self._enable_cluster_namespaces,
            },
            "absent": {
                "present": self._disable_cluster_namespaces,
                "notready": self._wait_cluster_disable,
                "absent": self._state_exit_unchanged,
            },
        }
        states[self.desired_state][self._current_state()]()

    def _state_exit_unchanged(self):
        """ Exits module execution unchanged """
        self.module.exit_json(changed=False)

    def _current_state(self):
        """
        Check current state of vSphere with K8s managed cluster
        Returns one of :
          ["present", "absent", "notready"]
        where "noteready" is when there is currently ongoing operation
        """
        self.cluster_info = self._get_cluster_info()
        if not self.cluster_info:
            return "absent"

        if self.cluster_info.config_status == nmc.Clusters.ConfigStatus.RUNNING:
            return "present"

        # Not very pretty but if we restart the module and there is operation in progress
        # validate that the correct operation is ongoing
        if (
            self.cluster_info.config_status == nmc.Clusters.ConfigStatus.REMOVING
            and self.desired_state == "present"
        ):
            self.module.fail_json(
                msg="K8s is being disabled in cluster while it should be enabled: {}".format(
                    self.cluster_name
                )
            )
        if (
            self.cluster_info.config_status == nmc.Clusters.ConfigStatus.CONFIGURING
            and self.desired_state == "absent"
        ):
            self.module.fail_json(
                msg="K8s is being enabled in cluster while it should be disabled: {}".format(
                    self.cluster_name
                )
            )

        # Return notready when the operation is in progress
        return "notready"

    def _enable_cluster_namespaces(self):
        """ Enable WCP on cluster """
        self._cluster_size_supported()
        self._cluster_supported()

        if self.module.params["network_provider"] == "NSXT_CONTAINER_PLUGIN":
            self.edge_cluster()

        enable_spec = self._enable_cluster_namespaces_spec()
        if not self.module.check_mode:
            try:
                self.namespaces_management_client.Clusters.enable(
                    cluster=self.cluster_id, spec=enable_spec
                )
            # TODO: Add a generic vapi_errors parser and enlist more specific exceptions
            except vapi_errors.AlreadyExists:
                # Note: This should never happen
                self.module.exit_json(
                    changed=False,
                    msg="Kubernetes arelady enabled on {}".format(self.cluster_name),
                )
            except Exception as e:
                self.module.fail_json(msg=to_native(str(e)))
            self._wait_cluster_enable()
        self.module.exit_json(
            changed=True, msg="Enabled K8s on cluster {}".format(self.cluster_name)
        )

    def _enable_cluster_namespaces_spec(self):
        """ Generate enable spec"""
        # TODO: Add master_dns_names, we don't use this yet
        enable_spec = self.namespaces_management_client.Clusters.EnableSpec()
        enable_spec.size_hint = nmc.SizingHint(self.module.params["cluster_size"])
        enable_spec.service_cidr = self.parsed_args["service_cidr"]

        if (
            self.module.params["network_provider"]
            not in nmc.Clusters.NetworkProvider.get_values()
        ):
            self.module.fail_json(
                msg="Unsupported provider type - {}. Please provide either NSXT_CONTAINER_PLUGIN or VSPHERE_NETWORK".format(
                    self.module.params["network_provider"]
                )
            )
        enable_spec.network_provider = nmc.Clusters.NetworkProvider(
            self.module.params["network_provider"]
        )

        if self.module.params["network_provider"] == "NSXT_CONTAINER_PLUGIN":
            enable_spec.ncp_cluster_network_spec = nmc.Clusters.NCPClusterNetworkEnableSpec(
                pod_cidrs=self.parsed_args["pod_cidrs"],
                ingress_cidrs=self.parsed_args["ingress_cidrs"],
                egress_cidrs=self.parsed_args["egress_cidrs"],
                cluster_distributed_switch=self.dvs_uuid,
                nsx_edge_cluster=self.edge_cluster.edge_cluster,
            )
        if self.module.params["network_provider"] == "VSPHERE_NETWORK":
            enable_spec.load_balancer_config_spec = nmc.LoadBalancers.ConfigSpec(
                **self.module.params["load_balancer_config_spec"]
            )
            self.module.params["workload_networks_spec"][
                "supervisor_primary_workload_network"
            ]["network_provider"] = nmc.Clusters.NetworkProvider(
                self.module.params["network_provider"]
            )

            vsphere_network = self.module.params["workload_networks_spec"][
                "supervisor_primary_workload_network"
            ]["vsphere_network"]
            user_workload_pg = find_dvspg_by_name(
                self.dvs, vsphere_network["portgroup"]
            )
            if not user_workload_pg:
                self.module.fail_json(
                    msg="vDS Port group for K8s workload network not found {}".format(
                        vsphere_network["portgroup"]
                    )
                )
            vsphere_network["portgroup"] = user_workload_pg.key

            enable_spec.workload_networks_spec = nmc.Clusters.WorkloadNetworksEnableSpec(
                **self.module.params["workload_networks_spec"]
            )

        enable_spec.master_management_network = self.parsed_args["management_network"]
        enable_spec.master_dns = self.module.params["master_dns_servers"]
        enable_spec.worker_dns = (
            self.module.params["worker_dns_servers"]
            or self.module.params["master_dns_servers"]
        )
        enable_spec.master_dns_search_domains = self.module.params[
            "master_dns_search_domains"
        ]
        enable_spec.master_ntp_servers = self.module.params["master_ntp_servers"]

        master_storage_policy = self._find_storage_policy(
            self.module.params["master_storage_policy"]
        ).policy
        enable_spec.master_storage_policy = master_storage_policy

        if self.module.params["ephemeral_storage_policy"]:
            enable_spec.ephemeral_storage_policy = self._find_storage_policy(
                self.module.params["ephemeral_storage_policy"]
            ).policy
        else:
            enable_spec.ephemeral_storage_policy = master_storage_policy

        enable_spec.login_banner = self.module.params["login_banner"]

        enable_spec.image_storage = nmc.Clusters.ImageStorageSpec(
            storage_policy=self._find_storage_policy(
                self.module.params["image_storage_policy"]
            ).policy
        )
        if self.module.params["default_image_registry"]:
            enable_spec.default_image_registry = nmc.Clusters.ImageRegistry(
                hostname=self.module.params["default_image_registry"]["hostname"],
                port=self.module.params["default_image_registry"]["port"],
            )
        enable_spec.default_image_repository = self.module.params[
            "default_image_repository"
        ]
        enable_spec.default_kubernetes_service_content_library = self._find_content_library(
            self.module.params["default_kubernetes_service_content_library"]
        ).id
        return enable_spec

    def _wait_cluster_enable(self):
        """ Wait for K8s to be enabled on vSphere cluster """
        # TODO: It seems that the config status could become in ERROR for some time
        #       Do more testing and find if we can handle this situation
        waited = 0
        self.cluster_info = self._get_cluster_info()

        while self.cluster_info.config_status != nmc.Clusters.ConfigStatus.RUNNING:
            sleep(self.WCP_CHECK_INTERVAL)
            waited += self.WCP_CHECK_INTERVAL
            self.cluster_info = self._get_cluster_info()
            if self.cluster_info.config_status == nmc.Clusters.ConfigStatus.ERROR:
                self.module.fail_json(
                    msg="Failed to enable k8s on cluster: {}\n  failure messages: {}".format(
                        self.cluster_name,
                        self._format_cluster_status_messages(
                            self.cluster_info.messages
                        ),
                    )
                )
            if waited > self.WCP_ENABLE_TIMEOUT:
                self.module.fail_json(
                    msg="Timedout enabling K8s on cluster {}".format(self.cluster_name)
                )

    def _get_cluster_info(self):
        """
        Get vSphere with K8s cluster detail info
        Return None if not enabled or unsupported
        """
        try:
            cluster_info = self.namespaces_management_client.Clusters.get(
                cluster=self.cluster_id
            )
        # If the cluster is not found or WCP is not enabled return None
        # NotFound cluster is not found
        except vapi_errors.NotFound:
            return None
        # Unsupported = WCP is not enabled according to documentation
        # NotFound is raised in both cases in testing
        except vapi_errors.Unsupported:
            return None
        except Exception as e:
            self.module.fail_json(
                msg="Failed to get cluster details for '{}': {}".format(
                    self.cluster_name, to_native(e)
                )
            )
        return cluster_info

    def _parse_arguments(self):
        """
        Parse and validate complex input arguments
        Initializes the parameters to API models
        """
        # Parse K8s required networks
        if self.module.params["network_provider"] == "NSXT_CONTAINER_PLUGIN":
            self.parsed_args["pod_cidrs"] = [
                self._parse_ipv4cidr(x, type="pod_cidr")
                for x in self.module.params["pod_cidrs"]
            ]
            self.parsed_args["ingress_cidrs"] = [
                self._parse_ipv4cidr(x, type="ingress_cidr")
                for x in self.module.params["ingress_cidrs"]
            ]
            self.parsed_args["egress_cidrs"] = [
                self._parse_ipv4cidr(x, type="egress_cidr")
                for x in self.module.params["egress_cidrs"]
            ]

        self.parsed_args["service_cidr"] = self._parse_ipv4cidr(
            self.module.params["service_cidr"], type="service_cidr"
        )
        self.parsed_args["management_network"] = self._parse_management_network(
            self.module.params["management_network"]
        )

    def _parse_management_network(self, management_network):
        """Parse input magemen_network attribute
        Returns: Clusters.NetworkSpec"""
        # Parse K8s management network
        try:
            subnet = ipaddress.ip_network(management_network["cidr"])
        except ValueError:
            self.module.fail_json(
                msg="Invalid management network CIDR provided: {}".format(
                    management_network["cidr"]
                )
            )
        subnet_hosts = list(subnet.hosts())
        mgmt_network = nmc.Clusters.NetworkSpec()
        mgmt_network.address_range = nmc.Clusters.Ipv4Range()

        # Set the 'address_count', default to 5 which the default vSphere 7.0 UI
        mgmt_network.address_range.address_count = (
            management_network["address_count"]
            if management_network["address_count"]
            else self.MGMT_NETWORK_ADDRESS_COUNT
        )
        if len(subnet_hosts) < (mgmt_network.address_range.address_count + 1):
            self.module.fail_json(
                msg="K8s management network too small {}".format(management_network)
            )

        mgmt_dvpg = find_dvspg_by_name(self.dvs, management_network["portgroup_name"])
        if not mgmt_dvpg:
            self.module.fail_json(
                msg="vDS Port group for K8s management network not found {}".format(
                    management_network["portgroup_name"]
                )
            )

        # Set the MGMT network mode to STATICRANGE, which is the only supported mode today
        mgmt_network.mode = nmc.Clusters.NetworkSpec.Ipv4Mode.STATICRANGE
        mgmt_network.network = mgmt_dvpg.key

        # Set the 'starting_address', default to the first IP in subnet
        if management_network["starting_address"]:
            if (
                ipaddress.IPv4Address(management_network["starting_address"])
                not in subnet_hosts
            ):
                self.module.fail_json(
                    msg="Provided management starting address is not in MGMT subnet"
                )
            mgmt_network.address_range.starting_address = management_network[
                "starting_address"
            ]
        else:
            mgmt_network.address_range.starting_address = str(subnet_hosts[0])

        # Set the 'default_gateway', default to the last IP address in subnet
        if management_network["gateway"]:
            if ipaddress.IPv4Address(management_network["gateway"]) not in subnet_hosts:
                self.module.fail_json(
                    msg="Provided management gateway is not in MGMT subnet"
                )
            mgmt_network.address_range.gateway = management_network["gateway"]
        else:
            mgmt_network.address_range.gateway = str(subnet_hosts[-1])

        mgmt_network.address_range.subnet_mask = str(subnet.netmask)

        return mgmt_network

    def _parse_ipv4cidr(self, cidr, type=None):
        """
        Parse network in CIDR format to API model
        namespace_management_client.Ipv4Cidr
        """
        try:
            network = ipaddress.ip_network(cidr)
        except ValueError:
            network_type_msg = ""
            if type:
                network_type_msg = " for {}".format(type)
            msg = (
                "Incorrect IP subnet CIDR provided"
                + network_type_msg
                + ": {}".format(cidr)
            )
            self.module.fail_json(msg=to_native(msg))
        return nmc.Ipv4Cidr(
            address=str(network.network_address), prefix=network.prefixlen
        )

    def _cluster_supported(self):
        """ Checks if cluster is supported """
        # network_provider="VSPHERE_NETWORK"
        clusters_compatability = self.namespaces_management_client.ClusterCompatibility.list(
            filter=nmc.ClusterCompatibility.FilterSpec(
                network_provider=nmc.Clusters.NetworkProvider(
                    self.module.params["network_provider"]
                )
            )
        )

        # There could be only one cluster with this MoRef, safe to index the list
        cluster_compatability = [
            x for x in clusters_compatability if x.cluster == self.cluster_id
        ][0]

        if not cluster_compatability.compatible:
            self.module.fail_json(
                msg="Cluster {} is not compatible for WCP\n  Reasons: {}".format(
                    self.cluster_name,
                    self._format_vapi_messages(
                        cluster_compatability.incompatibility_reasons
                    ),
                )
            )

        dvs_compatibility_list = self.namespaces_management_client.DistributedSwitchCompatibility.list(
            filter=nmc.DistributedSwitchCompatibility.FilterSpec(
                network_provider=nmc.Clusters.NetworkProvider(
                    self.module.params["network_provider"]
                )
            ),
            cluster=self.cluster_id,
        )

        dvs_id = (
            self.dvs_uuid
            if self.module.params["network_provider"] == "NSXT_CONTAINER_PLUGIN"
            else self.dvs._moId
        )

        # There could be only one dvSwitch with this MoRef, safe to index the list
        dvs_compatibility = [
            x for x in dvs_compatibility_list if x.distributed_switch == dvs_id
        ][0]

        if not dvs_compatibility.compatible:
            self.module.fail_json(
                msg="vDS {} is not compatible\n  {}".format(
                    self.dvs.name,
                    self._format_vapi_messages(
                        dvs_compatibility.incompatibility_reasons
                    ),
                )
            )

    def edge_cluster(self):
        edge_clusters = self.namespaces_management_client.EdgeClusterCompatibility.list(
            cluster=self.cluster_id, distributed_switch=self.dvs_uuid
        )

        # TODO: Move the EdgeCluster lookup into a separate method
        self.edge_cluster = None
        for edge_cluster in edge_clusters:
            if (
                edge_cluster.display_name
                == self.module.params["nsxt_edge_cluster_name"]
            ):
                self.edge_cluster = edge_cluster

        if not self.edge_cluster:
            self.module.fail_json(
                msg="NSX-T Edge cluster '{}' not found".format(
                    self.module.params["nsxt_edge_cluster_name"]
                )
            )

        if not self.edge_cluster.compatible:
            self.module.fail_json(
                "NSX-T Edge cluster '{}' not compatible\n  {}".foramt(
                    self.edge_cluster.display_name,
                    self._format_vapi_messages(
                        self.edge_cluster.incompatibility_reasons
                    ),
                )
            )

    def _find_dvs(self, dvs_name):
        """ Lookup dVS/vDS by name and return its object"""
        dvs = find_dvs_by_name(self.vsphere_client, dvs_name)
        if not dvs:
            self.module.fail_json(msg="dVS switch not found: {}".format(dvs_name))
        return dvs

    def _find_storage_policy(self, policy_name):
        """ Lookup Storage policy """
        # TODO: Figure out if we can have multiple storage policy with the same name
        storage_policies = self.api_client.vcenter.storage.Policies.list()
        policy = [x for x in storage_policies if x.name == policy_name]
        if not policy:
            self.module.fail_json(
                msg="Storage policy '{}' not found".format(policy_name)
            )
        return policy[0]

    def _find_content_library(self, library_name):
        """ Find a content library by name and return its object"""
        content_libraries = self.api_client.content.Library.list()
        library = None
        for library_id in content_libraries:
            l = self.api_client.content.Library.get(library_id)
            if l.name == library_name:
                library = l
                break
        if not library:
            self.module.fail_json(
                msg="Conent library '{}' not found".format(library_name)
            )
        return library

    def _cluster_size_supported(self):
        """ Checks if the provided cluster size is supported """
        requested_cluster_size = nmc.SizingHint(self.module.params["cluster_size"])
        supported_sizes = self.namespaces_management_client.ClusterSizeInfo.get()

        if not requested_cluster_size in supported_sizes:
            self.module.fail_json(
                msg="Incorrect cluster size provided {}\n  Supported: {}".format(
                    self.module.params["cluster_size"], ",".join(supported_sizes.keys())
                )
            )

    def _update_cluster(self):
        """ Update WCP cluster settings """
        # TODO: Add ability to update the cluster settings
        self._state_exit_unchanged()

    def _disable_cluster_namespaces(self):
        """ Disable WCP for cluster """
        if not self.module.check_mode:
            try:
                self.namespaces_management_client.Clusters.disable(self.cluster_id)
            except vapi_errors.NotFound:
                self.module.exit_json(
                    changed=False,
                    msg="K8s already disabled on cluster {}".format(self.cluster_name),
                )
            except Exception as e:
                self.module.fail_json(msg=to_native(e))
        self._wait_cluster_disable()
        self.module.exit_json(
            changed=True, msg="Disabled K8s on cluster: {}".format(self.cluster_name)
        )

    def _wait_cluster_disable(self):
        """ Wait for WCP to be disabled in vSphere cluster """
        waited = 0
        self.cluster_info = self._get_cluster_info()

        while self.cluster_info:
            sleep(self.WCP_CHECK_INTERVAL)
            waited += self.WCP_CHECK_INTERVAL
            self.cluster_info = self._get_cluster_info()
            if (
                self.cluster_info
                and self.cluster_info.config_status == nmc.Clusters.ConfigStatus.ERROR
            ):
                self.module.fail_json(
                    msg="Failed to disable k8s on cluster: {}\n  failure messages: {}".format(
                        self.cluster_name,
                        self._format_cluster_status_messages(
                            self.cluster_info.messages
                        ),
                    )
                )
            if waited > self.WCP_DISABLE_TIMEOUT:
                self.module.fail_json(
                    msg="Timedout disabling K8s on cluster {}".format(self.cluster_name)
                )

    def _format_cluster_status_messages(self, messages):
        """ Parse a list of Clusters.Message messages """
        # No support for localized error messages, only en_US
        # refer com.vmware.vapi.std_client.LocalizableMessage documentation
        result = "\n".join(
            ["{}: {}".format(x.severity, x.details.default_message) for x in messages]
        )
        return result

    def _format_vapi_messages(self, messages):
        """ Parse a list of vapi.std_client.LocalizableMessage """
        # No support for localized error messages, only en_US
        # refer com.vmware.vapi.std_client.LocalizableMessage documentation
        result = "\n".join([x.default_message for x in messages])
        return result

    def _namespaces_supported(self):
        """ Check if Namespaces are supported in ths vCenter """
        namespaces_supported = self.namespaces_management_client.HostsConfig.get()
        if namespaces_supported.namespaces_supported is not True:
            self.module.fail_json(
                msg="Namespaces are not supported in {}".format(self.vcenter)
            )
        if namespaces_supported.namespaces_licensed is not True:
            self.module.fail_json(
                msg="Namespaces are not licensed in {}".format(self.vcenter)
            )

    def _get_cluster_moref(self):
        """ Lookup vSphere cluster by name """
        cluster_id = self.get_cluster_by_name(self.datacenter_name, self.cluster_name)
        if cluster_id is None:
            self.module.fail_json(
                msg="vSphere Cluster {} not found".format(self.cluster_name)
            )
        return cluster_id


def main():
    argument_spec = VmwareRestClient.vmware_client_argument_spec()
    argument_spec.update(
        cluster_name=dict(type="str", required=True),
        datacenter_name=dict(type="str", required=True),
        dvs_name=dict(type="str", required=True),
        cluster_size=dict(type="str", required=False, default="TINY"),
        service_cidr=dict(type="str", required=True),
        pod_cidrs=dict(type="list", required=False, elements="str"),
        ingress_cidrs=dict(type="list", required=False, elements="str"),
        egress_cidrs=dict(type="list", required=False, elements="str"),
        nsxt_edge_cluster_name=dict(type="str", required=False),
        management_network=dict(
            type="dict",
            required=True,
            options=dict(
                cidr=dict(type="str", required=True),
                # TODO: Document that it will default to the first address if not provided
                starting_address=dict(type="str", required=False),
                # TODO: Document that it default to 5 (The UI workflow default)
                address_count=dict(type="int", required=False),
                # TODO: Document that it will default to the last IP address
                gateway=dict(type="str", required=False),
                portgroup_name=dict(type="str", required=True),
            ),
        ),
        master_dns_servers=dict(type="list", required=True, elements="str"),
        # TODO: Add documentation that it will default to master_dns_servers
        # TODO: Figure out if DNS severs are really needed for WCP
        worker_dns_servers=dict(type="list", required=False, elements="str"),
        master_dns_search_domains=dict(type="list", required=False, elements="str"),
        master_ntp_servers=dict(type="list", required=False, elements="str"),
        master_storage_policy=dict(type="str", required=True),
        # TODO: Document it defaults to master_storage_policy
        ephemeral_storage_policy=dict(type="str", required=False),
        # TODO: Consider the image_storage_policy to point to the master_storage_policy as well
        image_storage_policy=dict(type="str", required=True),
        default_image_registry=dict(
            type="dict",
            required=False,
            options=dict(
                hostname=dict(type="str", required=True),
                port=dict(type="int", required=False, default=443),
            ),
        ),
        default_image_repository=dict(type="str", required=False),
        # TODO: Is the Content Library really optional
        default_kubernetes_service_content_library=dict(typ="str", required=False),
        login_banner=dict(type="str", required=False),
        state=dict(type="str", choices=["present", "absent"], required=True),
        network_provider=dict(
            type="str",
            choices=["VSPHERE_NETWORK", "NSXT_CONTAINER_PLUGIN"],
            required=True,
        ),
        load_balancer_config_spec=dict(
            type="dict",
            required=False,
            options=dict(
                address_ranges=dict(
                    type="list",
                    required=True,
                    options=dict(
                        address=dict(type="str", required=True),
                        count=dict(type="int", required=True),
                    ),
                ),
                avi_config_create_spec=dict(
                    type="dict",
                    required=False,
                    options=dict(
                        certificate_authority_chain=dict(type="str", required=False),
                        password=dict(type="str", required=False),
                        server=dict(
                            type="dict",
                            required=False,
                            options=dict(
                                host=dict(type="str", required=False),
                                port=dict(type="int", required=False),
                            ),
                        ),
                        username=dict(type="str", required=False),
                    ),
                ),
                id=dict(type="str", required=False),
                provider=dict(type="str", choices=["AVI", "HA_PROXY"], required=False),
            ),
        ),
        workload_networks_spec=dict(
            type="dict",
            required=False,
            options=dict(
                supervisor_primary_workload_network=dict(
                    type="dict",
                    options=dict(
                        network=dict(type="str", required=True),
                        vsphere_network=dict(
                            type="dict",
                            options=dict(
                                address_ranges=dict(
                                    type="list",
                                    required=True,
                                    options=dict(
                                        address=dict(type="str", required=True),
                                        count=dict(type="int", required=True),
                                    ),
                                ),
                                gateway=dict(type="str", required=True),
                                portgroup=dict(type="str", required=True),
                                subnet_mask=dict(type="str", required=True),
                            ),
                        ),
                    ),
                )
            ),
        ),
    )

    required_if = [
        [
            "network_provider",
            "VSPHERE_NETWORK",
            ["load_balancer_config_spec", "workload_networks_spec"],
            True,
        ],
        [
            "network_provider",
            "NSXT_CONTAINER_PLUGIN",
            ["egress_cidrs", "ingress_cidrs", "nsxt_edge_cluster_name", "pod_cidrs"],
            True,
        ],
    ]

    module = AnsibleModule(
        argument_spec=argument_spec, supports_check_mode=True, required_if=required_if
    )
    if not HAS_LIB:
        module.fail_json(
            msg="vSphere Automation SDK for Python (com.vmware.vcenter.namespace_management_client) is required for this module"
        )

    try:
        vcenter_appliance_services = VmwareVcenterWcpCluster(module)
        vcenter_appliance_services.process_state()
    except Exception as e:
        module.fail_json(msg=to_native(str(e)))

if __name__ == "__main__":
    main()

