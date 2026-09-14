#!/bin/bash

set -e

PLATFORM=""
# Charms for integration tests
TEST_CHARMS=("tests/charms/dashboards_application_charm")
LIB_PATH="./single_kernel_opensearch_dashboards"
CHARMS_PATH="./tests/charms"


# --- Argument Parsing ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -p|--platform)
            PLATFORM="$2"
            shift 2
            ;;
        -c|--charm)
            TEST_CHARMS+=("$2")
            shift 2
            ;;
        *)
            # Maintain backward compatibility for an unnamed first parameter as the charm
            if [[ "$1" != -* ]] && [ ${#TEST_CHARMS[@]} -eq 0 ]; then
                TEST_CHARMS+=("$1")
                shift
            else
                echo "Unknown parameter passed: $1"
                echo "Usage: $0 [-p|--platform <platform>] [-c|--charm <charm_path>] [charm_path]"
                exit 1
            fi
            ;;
    esac
done

# Helper function to avoid code duplication
pack_charm() {
    # Store arguments in an array to safely handle spaces or empty strings
    local pack_args=("-v")

    # Inject platform argument if one was provided
    if [ -n "$PLATFORM" ]; then
        pack_args+=("--platform" "$PLATFORM")
    fi

    if ${CI_CACHE:-false}; then
        if ! command -v ccc >/dev/null 2>&1; then
              echo "Error: CI_CACHE is enabled but 'ccc' is not installed." >&2
              return 1
        fi
        ccc pack "${pack_args[@]}"
    else
        charmcraft pack "${pack_args[@]}"
    fi
}

if [ ${#TEST_CHARMS[@]} -eq 0 ]; then
    TEST_CHARMS=("${CHARMS_PATH}/dashboards_vm_charm")
fi

for directory in "${TEST_CHARMS[@]}"; do
    if [[ " ${TEST_CHARMS[*]} " =~ ${directory} ]]; then
      printf 'Building charm %s \n' "$directory"
      pushd "$directory"
      pack_charm
      popd
    else
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
    fi
done
