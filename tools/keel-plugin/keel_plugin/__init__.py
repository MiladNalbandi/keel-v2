"""keel-plugin: make, check, pack, sign and list keel plugins.

A standalone tool (stdlib + cryptography, no keel imports). The formats are keel's: the package and its manifest
(docs/plugins/02-plugin-package.md), minisign signatures and the catalog index (docs/plugins/13-step4-contract.md).
"""

__version__ = "0.1.0"


class PluginToolError(Exception):
    """Something the person must fix: the message says what, in plain words."""
