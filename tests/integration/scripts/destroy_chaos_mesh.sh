#!/bin/bash

# Utility script to removing chaosmesh from the K8S cluster, to clean up test artefacts

chaos_mesh_ns=$1

if [ -z "${chaos_mesh_ns}" ]; then
    exit 1
fi

destroy_chaos_mesh() {
    echo "deleting api-resources"
    for i in $(sudo k8s kubectl api-resources | grep chaos-mesh | awk '{print $1}'); do timeout 30 sudo k8s kubectl delete "${i}" --all --all-namespaces || :; done

    if [ "$(sudo k8s kubectl -n "${chaos_mesh_ns}" get mutatingwebhookconfiguration | grep -c 'choas-mesh-mutation')" = "1" ]; then
        echo "deleting chaos-mesh-mutation"
        timeout 30 sudo k8s kubectl -n "${chaos_mesh_ns}" delete mutatingwebhookconfiguration chaos-mesh-mutation || :
    fi

    if [ "$(sudo k8s kubectl -n "${chaos_mesh_ns}" get validatingwebhookconfiguration | grep -c 'chaos-mesh-validation-auth')" = "1" ]; then
        echo "deleting chaos-mesh-validation-auth"
        timeout 30 sudo k8s kubectl -n "${chaos_mesh_ns}" delete validatingwebhookconfiguration chaos-mesh-validation-auth || :
    fi

    if [ "$(sudo k8s kubectl -n "${chaos_mesh_ns}" get validatingwebhookconfiguration | grep -c 'chaos-mesh-validation')" = "1" ]; then
        echo 'deleting chaos-mesh-validation'
        timeout 30 sudo k8s kubectl -n "${chaos_mesh_ns}" delete validatingwebhookconfiguration chaos-mesh-validation || :
    fi

    if [ "$(sudo k8s kubectl get clusterrolebinding | grep 'chaos-mesh' | awk '{print $1}' | wc -l)" != "0" ]; then
        echo "deleting clusterrolebindings"
        timeout 30 sudo k8s kubectl delete clusterrolebinding "$(sudo k8s kubectl get clusterrolebinding | grep 'chaos-mesh' | awk '{print $1}')" || :
    fi

    if [ "$(sudo k8s kubectl get clusterrole | grep 'chaos-mesh' | awk '{print $1}' | wc -l)" != "0" ]; then
        echo "deleting clusterroles"
        timeout 30 sudo k8s kubectl delete clusterrole "$(sudo k8s kubectl get clusterrole | grep 'chaos-mesh' | awk '{print $1}')" || :
    fi

    if [ "$(sudo k8s kubectl get crd | grep 'chaos-mesh.org' | awk '{print $1}' | wc -l)" != "0" ]; then
        echo "deleting crds"
        timeout 30 sudo k8s kubectl delete crd "$(sudo k8s kubectl get crd | grep 'chaos-mesh.org' | awk '{print $1}')" || :
    fi

    if [ -n "${chaos_mesh_ns}" ] && [ "$(sudo k8s helm repo list --namespace "${chaos_mesh_ns}" | grep -c 'chaos-mesh')" = "1" ]; then
        echo "uninstalling chaos-mesh k8s helm repo"
        sudo k8s helm uninstall chaos-mesh --namespace "${chaos_mesh_ns}" || :
    fi
}

echo "Destroying chaos mesh in ${chaos_mesh_ns}"
destroy_chaos_mesh
