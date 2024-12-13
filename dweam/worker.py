import asyncio
from asyncio.subprocess import Process
import json
import os
from typing import Optional, Any
from datetime import datetime
from pathlib import Path
from asyncio import StreamReader, StreamWriter

from pydantic import TypeAdapter

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer
from av.video.frame import VideoFrame

from dweam.models import GameInfo
from dweam.utils.entrypoint import get_cache_dir
from dweam.log_config import get_logger
from dweam.utils.turn import create_turn_credentials, get_turn_stun_urls
from dweam.constants import JS_TO_PYGAME_KEY_MAP, JS_TO_PYGAME_BUTTON_MAP
from structlog.stdlib import BoundLogger
from dweam.commands import Command, Response, SchemaCommand, StopCommand, UpdateParamsCommand, HandleOfferCommand, OfferData, ErrorResponse

class GameWorker:
    def __init__(
        self,
        log: BoundLogger,
        game_info: GameInfo,
        session_id: str,
        local_only: bool,
        game_type: str,
        game_id: str,
        venv_path: Path,
    ):
        self.log = log
        self.game_info = game_info
        self.game_type = game_type
        self.game_id = game_id
        self.session_id = session_id
        self.local_only = local_only
        self.venv_path = venv_path

        self.last_heartbeat = datetime.now()
        self.cleanup_scheduled = False
        
        # Communication handles
        self.process: Optional[Process] = None
        self.reader: Optional[StreamReader] = None
        self.writer: Optional[StreamWriter] = None
        
        # WebRTC
        self.pc: Optional[RTCPeerConnection] = None

    async def _monitor_stdout(self):
        """Monitor stdout of the worker process and log any output"""
        if not self.process or not self.process.stdout:
            return
        
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            self.log.info("Worker stdout:", line=line.decode().rstrip())

    async def _monitor_stderr(self):
        """Monitor stderr of the worker process and log any output"""
        if not self.process or not self.process.stderr:
            return
            
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            self.log.info("Worker stderr:", error=line.decode().rstrip())

    async def start(self):
        """Start the worker process and establish communication"""
        venv_python = self.venv_path / "bin" / "python"
        worker_script = Path(__file__).parent / "game_process.py"
        
        # Create a TCP server socket
        client_connected = asyncio.Event()
        
        async def handle_client(reader, writer):
            self.reader = reader
            self.writer = writer
            client_connected.set()
            
        server = await asyncio.start_server(
            handle_client,
            host='127.0.0.1',
            port=0  # Let OS choose port
        )
        addr = server.sockets[0].getsockname()
        port = addr[1]
        self.log.info("Started TCP server", port=port)
        
        # Log what we're about to execute
        self.log.info("Starting worker process", 
                     python=str(venv_python),
                     script=str(worker_script),
                     game_type=self.game_type,
                     game_id=self.game_id)
        
        # Start the worker process with the port number
        self.process = await asyncio.create_subprocess_exec(
            str(venv_python),
            str(worker_script),
            self.game_type,
            self.game_id,
            json.dumps([]),  # Add empty ice servers
            str(port),  # Pass port number
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        self.log.info("Started worker process", pid=self.process.pid)
        
        # Start monitoring stderr and stdout immediately
        asyncio.create_task(self._monitor_stderr())
        asyncio.create_task(self._monitor_stdout())
        
        # Wait for client connection with timeout
        try:
            # Start serving (but don't block)
            async with server:
                server_task = asyncio.create_task(server.serve_forever())
                # Wait for client to connect with timeout
                await asyncio.wait_for(client_connected.wait(), timeout=5)
                # Once connected, cancel the server task
                server_task.cancel()
                try:
                    await server_task
                except asyncio.CancelledError:
                    pass
                    
            if not self.reader or not self.writer:
                raise RuntimeError("No connection received")
            self.log.info("Client connected")
            
        except asyncio.TimeoutError:
            self.log.error("Timeout waiting for worker to connect")
            # Check if process has failed
            if self.process.returncode is not None:
                self.log.error("Worker process failed", returncode=self.process.returncode)
            raise
        except Exception as e:
            self.log.error("Error during connection", error=str(e))
            raise

    async def run(self, offer: RTCSessionDescription) -> RTCSessionDescription:
        """Set up and run the WebRTC connection"""
        if not self.process:
            await self.start()

        # Pass the offer to game process and get answer
        response = await self._send_command(HandleOfferCommand(
            cmd="handle_offer",
            data=OfferData(sdp=offer.sdp, type=offer.type)
        ))
        
        # Convert response to RTCSessionDescription
        return RTCSessionDescription(sdp=response["sdp"], type=response["type"])

    async def _send_command(self, command: Command) -> Any:
        """Send a command to the worker process and get the response"""
        if not self.writer or not self.reader:
            raise RuntimeError("Worker process not started")
        
        message = command.model_dump_json() + "\n"
        self.writer.write(message.encode())
        await self.writer.drain()
        
        response = await self.reader.readline()
        if response == b"":
            raise RuntimeError("Worker process closed")
        
        self.log.info("Worker response", response=response)
        result = TypeAdapter(Response).validate_json(response)
        
        if isinstance(result, ErrorResponse):
            raise ValueError(result.error)
        return result.data

    async def get_params_schema(self) -> dict[str, Any]:
        """Get the JSON schema for game parameters"""
        if not self.process:
            await self.start()
        return await self._send_command(SchemaCommand())

    async def update_params(self, params: dict) -> None:
        """Update game parameters"""
        if not self.process:
            await self.start()
        return await self._send_command(UpdateParamsCommand(data=params))

    async def cleanup(self):
        """Clean up worker resources"""
        if self.cleanup_scheduled:
            return
        
        self.cleanup_scheduled = True
        try:
            if self.writer:
                try:
                    await self._send_command(StopCommand())
                except:
                    pass
                self.writer.close()
                await self.writer.wait_closed()
            
            # Close socket
            if hasattr(self, '_socket'):
                self._socket.close()
            
            if self.process and self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    
        except Exception as e:
            self.log.error("Error during cleanup", error=str(e)) 