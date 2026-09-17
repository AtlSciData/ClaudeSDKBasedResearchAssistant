"""
Windows-only launcher. uvicorn's --reload (and its default loop setup on
Windows generally) ends up on SelectorEventLoop, which doesn't support
subprocesses -- but our agent needs to spawn mcp_server.py as a subprocess.
This forces ProactorEventLoop instead, which does support them.
Not needed later in Docker/Linux -- there, the plain `uvicorn app.main:app`
command in the Dockerfile works fine as-is.
"""
import asyncio
from uvicorn import Config, Server


class ProactorServer(Server):
    def run(self, sockets=None):
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.serve(sockets=sockets))
        finally:
            loop.close()


if __name__ == "__main__":
    config = Config("backend.app.main:app", host="127.0.0.1", port=8080)
    ProactorServer(config=config).run()