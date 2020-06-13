import subprocess
import types
import time
import socketio
import pty
import select
import os
import sys
import signal
import asyncio
from engineio import asyncio_socket, packet

signal.signal(signal.SIGCHLD, lambda signum, bt: os.waitpid(-1, os.WNOHANG))

class TerminalNamespace(socketio.AsyncNamespace):
    def __init__(self, namespace, sio):
        super().__init__(namespace)
        self.sio = sio
        self.ext = {"python3":".py", "clang":".c"}
    
    async def on_connect(self, sid, environ):
        await self.sio.emit('test', {'data': 'hi'}, to=sid)

    
    async def on_disconnect(self, sid):
        print(f"disconnect {sid}")
        session = await self.sio.get_session(sid)
        if session:
            # os.killpg(os.getpgid(session['child_pid']), signal.SIGKILL)
            # os.write(session['fd'], "exit\n".encode())
            try:
                p = subprocess.Popen(["docker", "stop", sid], stdin=None, stdout=None, stderr=None, close_fds=True)
                p.communicate(timeout=5)
                print(f"stopped container {sid}")
            except subprocess.TimeoutExpired:
                print("timeout when stop container")
                p.kill()
                print("killed stop container process")
    
    
    async def wait_for_idle(self, session_id, caller, **kargs):
        try:
            session = await self.sio.get_session(session_id)

            while not session['idle']:
                print('wait_for_idle', caller.__name__)
                session = await self.sio.get_session(session_id)
                await asyncio.sleep(0.5)

            session['idle'] = False
            await self.sio.save_session(session_id, session)

            await caller(**kargs)

            session['idle'] = True
            await self.sio.save_session(session_id, session)
        except KeyError:
            pass

    
    async def save_code(self, sid, data):
        tmp_filename = f"{sid}_{data['lang']}"
        with open(tmp_filename, "w") as f:
            f.write(data["code"])
        
        subprocess.run(["docker", "cp", tmp_filename, f"{sid}:root/main{self.ext[data['lang']]}"])

        os.remove(tmp_filename)

    
    async def on_save_code(self, sid, data):
        await self.wait_for_idle(sid, self.save_code, sid=sid, data=data)

    
    async def compile_code(self, sid, data):
        session = await self.sio.get_session(sid)
        
        os.write(session['fd'], f"clear\n".encode())

        if data['lang'] == 'python3':
            os.write(session['fd'], f"python3 ~/main{self.ext[data['lang']]}\n".encode())
        elif data['lang'] == 'clang':
            os.write(session['fd'], f"gcc -o main ~/main{self.ext[data['lang']]} && ./main\n".encode())\

    
    async def on_compile_code(self, sid, data):
        await self.wait_for_idle(sid, self.compile_code, sid=sid, data=data)

    
    async def on_create_terminal(self, sid):
        try:
            session = await self.sio.get_session(sid)

            if session:
                return

            (child_pid, fd) = pty.fork()

            if child_pid == 0:
                subprocess.run(["docker", "run", "-it", "--rm", "--name", sid, "ctmanjak/codedu_base:terminal"])
            else:
                print("opening a new session")

                print("connect: child pid is", child_pid)

                task = self.sio.start_background_task(
                    target=self.read_output, sid=sid
                )
                
                await self.sio.save_session(sid, {"idle":None,"fd":fd, "child_pid":child_pid, "task":task})

                print("connect: task started")

                self.sio.eio.sockets[sid].on_terminal = True
        except KeyError:
            pass

    
    async def on_client_input(self, sid, data):
        try:
            session = await self.sio.get_session(sid)
            
            if session:
                file_desc = session["fd"]
                if file_desc:
                    os.write(file_desc, f"{data['input']}".encode())
                    await self.sio.save_session(sid, session)
        except KeyError:
            pass
                        
    
    async def read_output(self, sid):
        max_read_bytes = 1024 * 2
        
        while True:
            try:
                session = await self.sio.get_session(sid)

                await self.sio.sleep(0.01)

                if session:
                    file_desc = session["fd"]

                    if file_desc:
                        timeout_sec = 0
                        (data_ready, _, _) = select.select([file_desc], [], [], timeout_sec)
                        if data_ready:
                            if session['idle'] == None:
                                session['idle'] = True
                                await self.sio.save_session(sid, session)
                            output = os.read(file_desc, max_read_bytes).decode()
                            if output:
                                await self.emit_lock(
                                    "client_output",
                                    {
                                        "output": output,
                                    },
                                    sid,
                                )
            except KeyError:
                break
            except OSError:
                await self.sio.disconnect(sid)
                break

    
    async def _emit_lock(self, event, data, sid):
        await self.sio.emit(
                event,
                data,
                to=sid,
            )

    
    async def emit_lock(self, event, data, sid):
        await self.wait_for_idle(sid, self._emit_lock, event=event, data=data, sid=sid)

async def receive(self, pkt):
    """Receive packet from the client."""
    self.server.logger.info('%s: Received packet %s data %s',
                            self.sid, packet.packet_names[pkt.packet_type],
                            pkt.data if not isinstance(pkt.data, bytes)
                            else '<binary>')
    if pkt.packet_type == packet.PING:
        self.last_ping = time.time()
        if not self.on_terminal:
            await self.close()
        else:
            await self.send(packet.Packet(packet.PONG, pkt.data))
    elif pkt.packet_type == packet.MESSAGE:
        await self.server._trigger_event(
            'message', self.sid, pkt.data,
            run_async=self.server.async_handlers)
    elif pkt.packet_type == packet.UPGRADE:
        await self.send(packet.Packet(packet.NOOP))
    elif pkt.packet_type == packet.CLOSE:
        await self.close(wait=False, abort=True)
    else:
        raise exceptions.UnknownPacketError()

async def _handle_connect(self, environ, transport, b64=False,
                            jsonp_index=None):
    """Handle a client connection request."""
    if self.start_service_task:
        # start the service task to monitor connected clients
        self.start_service_task = False
        self.start_background_task(self._service_task)

    sid = self._generate_id()
    s = asyncio_socket.AsyncSocket(self, sid)
    
    s.receive = types.MethodType(receive, s)
    s.on_terminal = False
    self.sockets[sid] = s

    pkt = packet.Packet(
        packet.OPEN, {'sid': sid,
                        'upgrades': self._upgrades(sid, transport),
                        'pingTimeout': int(self.ping_timeout * 1000),
                        'pingInterval': int(self.ping_interval * 1000)})
    await s.send(pkt)

    ret = await self._trigger_event('connect', sid, environ,
                                    run_async=False)
    if ret is not None and ret is not True:
        del self.sockets[sid]
        self.logger.warning('Application rejected connection')
        return self._unauthorized(ret or None)

    if transport == 'websocket':
        ret = await s.handle_get_request(environ)
        if s.closed:
            # websocket connection ended, so we are done
            del self.sockets[sid]
        return ret
    else:
        s.connected = True
        headers = None
        if self.cookie:
            headers = [('Set-Cookie', self.cookie + '=' + sid)]
        try:
            return self._ok(await s.poll(), headers=headers, b64=b64,
                            jsonp_index=jsonp_index)
        except exceptions.QueueEmpty:
            return self._bad_request()

sio = socketio.AsyncServer(async_mode='asgi')
sio.register_namespace(TerminalNamespace('/', sio))
socket = socketio.ASGIApp(sio)

sio.eio._handle_connect = types.MethodType(_handle_connect, sio.eio)