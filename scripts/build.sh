#!/bin/bash

set -e

LIB_PATH="./single_kernel_opensearch_dashboards"
SPECIAL_CHARM="tests/integration/application_charm"
CHARMS_PATH="./tests/charms"

# Helper function to avoid code duplication
pack_charm() {
    if ${CI_CACHE:-false}; then
        ccc pack -v
    else
        charmcraft pack -v
    fi
}

if [ $# -ge 1 ]; then
    declare -a TEST_CHARMS=("$1")
else
    declare -a TEST_CHARMS=("${CHARMS_PATH}/vm" )
fi

for directory in "${TEST_CHARMS[@]}"; do
    # Check if the current directory matches the special charm path
    # We use wildcard * to catch it even if the user passes "./tests/..."
    if [[ "$directory" == *"$SPECIAL_CHARM" ]]; then
        printf 'Building charm %s \n'"${directory}"
        pushd "$directory"
        pack_charm
        popd

        # Skip the rest of the loop for this iteration
        continue
    fi

    echo "clearing out libs for charm"
    directory_lib_path="${directory}/${LIB_PATH}"
    rm -rf "$directory_lib_path"
    mkdir "$directory_lib_path"
    echo "copying over libs from single kernel charm"
    cp -r "${LIB_PATH}" "$directory_lib_path"
    cp "pyproject.toml" "$directory_lib_path"
    cp "README.md" "$directory_lib_path"

    printf 'Building charm %s \n'"${directory}"


    pushd "$directory"

    # Backup files
    cp pyproject.toml pyproject.toml.backup
    cp poetry.lock poetry.lock.backup

    # Disable strict mode for build test lib.
    pushd "${LIB_PATH}"
    git init
    sed 's/strict = true/strict = false/' -i "pyproject.toml"
    popd

    poetry add "${LIB_PATH}/"
    poetry lock

    python3 -c 'import pathlib; import shutil; import subprocess; git_hash=subprocess.run(["git", "describe", "--always", "--dirty"], capture_output=True, check=True, encoding="utf-8").stdout; file = pathlib.Path("charm_version"); shutil.copy(file, pathlib.Path("charm_version.backup")); version = file.read_text().strip(); file.write_text(f"{version}+{git_hash}")'

    # Pack the charm
    pack_charm

    # Cleanup
    echo "removing copied files from single kernel charm."
    rm ${LIB_PATH} -rf
    mv charm_version.backup charm_version
    mv pyproject.toml.backup pyproject.toml
    mv poetry.lock.backup poetry.lock

    # Go back to root directory
    popd
done