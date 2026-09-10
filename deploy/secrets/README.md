# Runtime secrets

No secret value belongs in this directory or in the repository. Development
secrets are generated with mode 0600 under the ignored `tmp/runtime-secrets/`
directory. Production deployments set the `*_HOST_FILE` variables to files
managed outside the checkout and Docker build context.
