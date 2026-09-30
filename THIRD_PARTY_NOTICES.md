# Third-party notices

Doosra's desktop app includes, or downloads on first run, the following. Each is used under its own licence.

## Bundled with the app

| Component | Licence | Source |
| --- | --- | --- |
| llama.cpp (`llama-server`, ggml; Vulkan and CPU builds, release b11249) | MIT | <https://github.com/ggml-org/llama.cpp> |
| LLVM OpenMP runtime (shipped inside the llama.cpp CPU build; its licence file is next to it) | Apache-2.0 WITH LLVM-exception | <https://openmp.llvm.org> |
| Python 3 runtime | PSF License | <https://www.python.org> |
| FastAPI, Starlette, Pydantic, Uvicorn | MIT / BSD-3-Clause | <https://fastapi.tiangolo.com> |
| DuckDB | MIT | <https://duckdb.org> |
| pandas, NumPy | BSD-3-Clause | <https://pandas.pydata.org>, <https://numpy.org> |
| SQLAlchemy | MIT | <https://www.sqlalchemy.org> |
| LangGraph | MIT | <https://github.com/langchain-ai/langgraph> |
| Model Context Protocol Python SDK | MIT | <https://github.com/modelcontextprotocol/python-sdk> |
| OpenAI Python client | Apache-2.0 | <https://github.com/openai/openai-python> |
| pywebview | BSD-3-Clause | <https://pywebview.flowrl.com> |
| pythonnet | MIT | <https://github.com/pythonnet/pythonnet> |
| React, React Router | MIT | <https://react.dev> |
| Recharts | MIT | <https://recharts.org> |
| react-markdown, remark-gfm | MIT | <https://github.com/remarkjs> |

The app's window uses Microsoft Edge WebView2, which is part of Windows and is not redistributed by Doosra.

## Downloaded on first run

| Component | Licence | Source |
| --- | --- | --- |
| Qwen3.5 9B / 4B (GGUF, Q4_K_M) | Apache-2.0 | <https://huggingface.co/lmstudio-community> (weights by the Qwen team, Alibaba Cloud) |
| Cricket data | Open Data Commons Attribution License (ODC-By 1.0) | Ball-by-ball data from Cricsheet, <https://cricsheet.org> |

The MIT licence text of llama.cpp:

> Copyright (c) 2023-2026 The ggml authors
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
> documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
> rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
> persons to whom the Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
> Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
> WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
> COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
> OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
