# Companion tools in the development checkout

Some local development workflows use standalone tools or neighboring
checkouts. These are optional and are not required for the core Django package.

## Rust report PDF renderer

The source is under `tools/report_pdf_renderer_rust`. From the repository root,
use the Make targets to build and run its example in `devenv`:

```bash
make report-renderer-run-example-devenv
```

To install the renderer through the configured development environment and
print the backend integration environment settings:

```bash
make report-renderer-install-devenv
make -s report-renderer-env
```

Review the target definitions in the repository `Makefile` for the exact
installation path and integration variables.

## Terminology editor checkout

If the optional `lx-terminology-editor` repository is checked out beside this
one, its local development server can be run with its own `devenv` setup:

```bash
cd ../lx-terminology-editor
direnv allow
devenv shell
python server.py
```

The server is typically available at `http://localhost:4173`. The editor can
publish terminology bundles and a local registry beneath its `.published/`
directory. Treat those as generated local artifacts and follow that
repository's instructions for publication and lifecycle.
