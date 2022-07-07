Service installer : 
    https://docs.vmware.com/en/Service-Installer-for-VMware-Tanzu/1.2/service-installer/GUID-WhatsNew.html

for airgapped 
    télécharger:
        - SIVT v1.2 (Service installer for VMware Tanzu)
        - controller-20.1.7-9154.ova
        - Harbor ova (from ESE Team)
        
    Créer une content lib du nom souhaité pour y déposer l'ova de controller-avi (controller-20.1.7-9154.ova)

    Créer une content lib : SubscribedAutomation-Lib

    ATTENTION : Bien être attentif au prérequis réseau. Les réseaux doivent être bien configurés.
    
    Pousser le contenu dans la contentlib SubscribedAutomation-Lib (4 items) - en même  - https://docs.vmware.com/en/VMware-vSphere/7.0/vmware-vsphere-with-tanzu/GUID-E8C37D8A-E261-44F0-9947-45ABAB526CF3.html
        - photon-ova-disk1.vmdk
        - photon-ova.ovf
        - photon-ova.cert
        - photon-ova.mf
        
    Bien vérifier les prérequis de SIVT et créer les port groups dans le vcenter associés aux VLAN qui ont été définis
        
    Accéder à l'interface web de SIVT et suivre les instructions
    
A la fin du processus, un fichier json est généré et la commande suivante doit être lancée depuis la VM SIVT pour installer avi-controller, le configurer et activer le service WCP (Workload Control Plane)

$ ssh root@SIVT 
$ arcas --env vsphere --file /path/to/vsphere-dvs-tkgs-wcp.json --avi_configuration --avi_wcp_configuration --enable_wcp --verbose

Une fois cette commande terminée, il faut désormais créer un Vsphere namespace et déployer un cluster TKC. Cela peut être fait depuis la console SIVT

To Deploy Supervisor Namespace and Workload Clusters

# arcas --env vsphere --file /path/to/vsphere-dvs-tkgs-namespace.json --create_supervisor_namespace --create_workload_cluster --deploy_extentions --verbose


Specificité Airgapped Harbor :
    1 - Déployer harbor as a VM (https://via.vmw.com/tanzu-ese-assets - voir OVA HARBOR 2.x)
    2 - Relocaliser les images Tanzu dans le Harbor VM (https://rguske.github.io/post/deploy-tanzu-packages-from-a-private-registry/)
    3 - Récupérer le certificat de Harbor VM (ca.crt - kubectl -n tanzu-system-registry get secret harbor-tls -o=jsonpath="{.data.ca\.crt}" | base64 -d) pour le propager à ton Tanzu Kubernetes Cluster - Attention a bien le coder en base64 (k edit tkc toto -n tata ou k apply -f monfichier.yaml)
    4 - Déploiement de kapp-controller sur le TKC dédié pour héberger harbor en tant que package + configuration package (tanzu package add repo ... ) & installation de harbor en tant que tanzu package (prérequis : cert-manager & contour)
    5 - Récupérer le ca.crt (kubectl -n tanzu-system-registry get secret harbor-tls -o=jsonpath="{.data.ca\.crt}" | base64 -d) + encodage base64 pour ajouter dans configuration globale des clusters créés par la suite - k edit TkgServiceConfiguration
    
k get TkgServiceConfiguration tkg-service-configuration -o yaml

apiVersion: run.tanzu.vmware.com/v1alpha2
kind: TkgServiceConfiguration
metadata:
  name: tkg-service-configuration
spec:
  defaultCNI: antrea
  trust:
    additionalTrustedCAs:
    - data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSURXakNDQWtLZ0F3SUJBZ0lRUVFLdGZEckdNZithZVN0NVlMcEx3VEFOQmdrcWhraUc5dzBCQVFzRkFEQXQKTVJjd0ZRWURWUVFLRXc1UWNtOXFaV04wSUVoaGNtSnZjakVTTUJBR0ExVUVBeE1KU0dGeVltOXlJRU5CTUI0WApEVEl5TURZeE9USXdNekV4TUZvWERUTXlNRFl4TmpJd016RXhNRm93TFRFWE1CVUdBMVVFQ2hNT1VISnZhbVZqCmRDQklZWEppYjNJeEVqQVFCZ05WQkFNVENVaGhjbUp2Y2lCRFFUQ0NBU0l3RFFZSktvWklodmNOQVFFQkJRQUQKZ2dFUEFEQ0NBUW9DZ2dFQkFNM0RjeTRpRFpTeWUxdFRNZUhsZTN1d2FWa1RiaFVuSmkrSDVuenlqSUIwRGRNTwp0aEdUS2RKTXpjSFV1ekxHNkxRaUxRYnhXalEyTHhWYjJBL0NxbUU3WlJaMHBodGx2a1BYSFY0Z1ZmbUpES2lvCmNxY0VOUHl3YzgxKzJhTXlLL1JZejBoK3ZrcURIZDNFcXJRZ2dCNXN0aVBOTVdTWTBBVHB3T2ZuZ25qMHRzcVQKcmt1WldEZ1I2dUhjc3BvbkZCeU9MQXJVTXJBOHJQbldnN2lpY2ZreVlYMFI3eUJFajlDVlFUUEI4RUpBV05OYwptaVp6VzY1b3FxVjJ3S01JUmlsOWw5eFdkNVFyK0RMa2UzeEpRVndrekV2YTVXbDhXK1FCbVRmeThra01QTTFhCnU4VWg2dXdWMnM1TWEwdTlCRUpKdUxNUHl6eEJneklSdS9nSEk1TUNBd0VBQWFOMk1IUXdEZ1lEVlIwUEFRSC8KQkFRREFnSUVNQjBHQTFVZEpRUVdNQlFHQ0NzR0FRVUZCd01CQmdnckJnRUZCUWNEQWpBUEJnTlZIUk1CQWY4RQpCVEFEQVFIL01CMEdBMVVkRGdRV0JCUVFlQmorQS9vVElCSmZqTjNmT25MbmwrNnZCekFUQmdOVkhSRUVEREFLCmdnaG9ZWEppYjNKallUQU5CZ2txaGtpRzl3MEJBUXNGQUFPQ0FRRUFpRXp0NHU5TlozZDlabXc4M09MTlRRZmoKSnpFSE01VXZ4ZnUwNDZtL3g3Sm1GUWhaZVRkWGVqd3kxaENyWC96MmJGbWNLNzRLTkdpRjdEVVhMRXZjRHJrMwprRGZNR0Q4Q2oxdzBmNUFWdzVCcGpPWHZuL2loeTNDRGxoRHZ2TnlqZUNKeHNab1ZWV2NSdHNjTXgyb2lycUVGCjNNQVRjT08xbldncjNtVkI1WlovbmM2T0ZFWUJXSEQ4eVhGU2YrZFhlU3gwNXVDMk5OWjkyR0pVQzliaEJyVisKdXZlWE9nRDZsUThHSGJQaC93SE5KMzVKZDJWYWJ3WkpvVVJqT3JHeXBncXl4RG04aFhWdEpIODAzMzlBNWF5ZQpRZkRNbCs2cVpZTVE0SmEycStYejFJaEhZMUdRaHdCZlh4dVVBR1ZCVnF6aUJ3K0c3Z2pvWFkyaGdzbStLdz09Ci0tLS0tRU5EIENFUlRJRklDQVRFLS0tLS0K
      name: harbor

    6 - Relocalisation des images de Harbor VM sur Habor tanzu package
        Utiliseer la réplication harbor 
        
    7 - Tester la création d'un TKC qui doit hériter de la conf certificat harbor
        Pull d'une image nginx
            kubectl create deployment nginx --image harbor.h2o-4-586.h2o.vmware.com/myproject/nginx:latest -n default
            Ne pas oublier : kubectl create clusterrolebinding default-tkg-admin-privileged-binding --clusterrole=psp:vmware-system-privileged --group=system:authenticated
            
    8 - Switch off VM Harbor
