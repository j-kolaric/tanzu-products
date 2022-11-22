# 00-set-environment-variables.sh
#
# Run this script by running "source 00-set-environment-variables.sh"
#

# Tanzunet loging credentials
export TANZU_NET_USER=jkolaric@vmware.com
export TANZU_NET_PASSWORD=<Tanzu Network Password>

# Private registry details, used for TAP installation and storing container images
export MY_REGISTRY=registry.cloud-garage.net
export MY_REGISTRY_USER=jkolaric
export MY_REGISTRY_PASSWORD=<Private registry password>
export MY_REGISTRY_INSTALL_REPO=jkolaric/tap-packages-13

# TAP Version you're installing, this case 1.3.0
export TAP_VERSION=1.3.0
