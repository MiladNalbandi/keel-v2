# Notice

Most of the files in this folder were copied from **keel v1**
(https://github.com/MiladNalbandi/keel, commit `a9ed9e3`) and then edited for keel v2: the agents,
the knowledge and stack skills with their references and examples, the stacks, the packs and the
templates. The edits remove what only makes sense inside keel v1 (its slash commands, its `keel`
CLI calls and its state file) and say what keel v2 does instead.

keel v1 is licensed under the MIT License:

```
MIT License

Copyright (c) 2026 Milad Nalbandi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## CodeGraph

The keel v2 image installs **CodeGraph** (`@colbymchenry/codegraph`, version 1.6.2,
https://github.com/colbymchenry/codegraph) unchanged. keel runs it to index a project's symbols, calls and imports
(`codegraph init`, `sync`, `index`, `status`) and gives agents its MCP server (`codegraph serve --mcp`). Its anonymous
telemetry is turned off in the image (`CODEGRAPH_TELEMETRY=0`, `DO_NOT_TRACK=1`).

CodeGraph is licensed under the MIT License (Copyright (c) Colby McHenry); the full text ships with the package
(`npm view @colbymchenry/codegraph license`).
