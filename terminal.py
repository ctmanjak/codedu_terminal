import urllib
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
from engineio import asyncio_socket, packet, exceptions

# signal.signal(signal.SIGCHLD, lambda signum, bt: os.waitpid(-1, os.WNOHANG))

class TerminalNamespace(socketio.AsyncNamespace):
    def __init__(self, namespace, sio):
        super().__init__(namespace)
        self.sio = sio
        self.ext = {"python3":".py", "clang":".c"}
    

    # async def on_connect(self, sid, environ):
    #     await self.sio.emit('test', {'data': 'hi'}, to=sid)

    
    async def on_disconnect(self, sid):
        print(f"disconnect {sid}")
        session = await self.get_session(sid)
        if session:
            session['task'].cancel()
            try:
                p = subprocess.Popen(["docker", "stop", "--time", "5", sid], stdin=None, stdout=None, stderr=None, close_fds=True)
                p.communicate(timeout=5)
                print(f"stopped container {sid}")
            except subprocess.TimeoutExpired:
                print("timeout when stop container")
                p.kill()
                print("killed stop container process")
                os.kill(session['child_pid'], signal.SIGKILL)
    
    
    async def wait_for_idle(self, session_id, caller, **kargs):
        try:
            session = await self.get_session(session_id)
            if session:
                while not session['idle']:
                    print('wait_for_idle', caller.__name__)
                    session = await self.get_session(session_id)
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
        session = await self.get_session(sid)
        
        os.write(session['fd'], f"clear\n".encode())

        if data['lang'] == 'python3':
            os.write(session['fd'], f"python3 ~/main{self.ext[data['lang']]}\n".encode())
        elif data['lang'] == 'clang':
            os.write(session['fd'], f"gcc -o main ~/main{self.ext[data['lang']]} && ./main\n".encode())\

    
    async def on_compile_code(self, sid, data):
        await self.wait_for_idle(sid, self.compile_code, sid=sid, data=data)

    
    async def on_create_terminal(self, sid, data):
        try:
            session = await self.get_session(sid)

            if session:
                return

            (child_pid, fd) = pty.fork()

            if child_pid == 0:
                subprocess.run(["docker", "run", "-it", "--rm", "--name", sid, "-e", f"COLUMNS={data['columns']}", "-e", f"LINES={data['rows']}", "-e", "TERM=screen-256color", "ctmanjak/codedu_base:terminal"])
                sys.exit(0)
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
            session = await self.get_session(sid)
            
            if session:
                file_desc = session["fd"]
                if file_desc:
                    os.write(file_desc, f"{data['input']}".encode())
                    await self.sio.save_session(sid, session)
        except KeyError:
            pass
                        
    
    async def get_session(self, sid):
        session = None
        try:
            if sid in self.sio.eio.sockets:
                session = await self.sio.get_session(sid)
        except KeyError as e:
            print("get_session", e)

        return session

    async def read_output(self, sid):
        max_read_bytes = 1024 * 2
        
        while True:
            try:
                session = await self.get_session(sid)
                
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
                else: break
            except KeyError as e:
                print("read_output", e)
                break
            except OSError as e:
                print("read_output", e)
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
        if hasattr(self, "first_ping") and not self.on_terminal:
            print("close")
            await self.close()
        else:
            self.first_ping = None
            self.last_ping = time.time()
            await self.send(packet.Packet(packet.PONG, pkt.data))
        # await self.send(packet.Packet(packet.PONG, pkt.data))
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
        except exceptions.QueueEmpty as e:
            print("_handle_connect", e)
            return self._bad_request()

async def handle_request(self, *args, **kwargs):
    """Handle an HTTP request from the client.
    This is the entry point of the Engine.IO application. This function
    returns the HTTP response to deliver to the client.
    Note: this method is a coroutine.
    """
    translate_request = self._async['translate_request']
    if asyncio.iscoroutinefunction(translate_request):
        environ = await translate_request(*args, **kwargs)
    else:
        environ = translate_request(*args, **kwargs)
    HTTP_X_FORWARDED_FOR = environ.get("HTTP_X_FORWARDED_FOR", None)
    if HTTP_X_FORWARDED_FOR:
        print(f"X-Forwarded-For: {HTTP_X_FORWARDED_FOR}")
    else:
        print(f"Client IP: {args[0]['client'][0]}")
    if self.cors_allowed_origins != []:
        # Validate the origin header if present
        # This is important for WebSocket more than for HTTP, since
        # browsers only apply CORS controls to HTTP.
        origin = environ.get('HTTP_ORIGIN')
        if origin:
            allowed_origins = self._cors_allowed_origins(environ)
            if allowed_origins is not None and origin not in \
                    allowed_origins:
                self.logger.info(origin + ' is not an accepted origin.')
                r = self._bad_request()
                make_response = self._async['make_response']
                if asyncio.iscoroutinefunction(make_response):
                    response = await make_response(
                        r['status'], r['headers'], r['response'], environ)
                else:
                    response = make_response(r['status'], r['headers'],
                                                r['response'], environ)
                return response

    method = environ['REQUEST_METHOD']
    query = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
    sid = query['sid'][0] if 'sid' in query else None
    b64 = False
    jsonp = False
    jsonp_index = None

    if 'b64' in query:
        if query['b64'][0] == "1" or query['b64'][0].lower() == "true":
            b64 = True
    if 'j' in query:
        jsonp = True
        try:
            jsonp_index = int(query['j'][0])
        except (ValueError, KeyError, IndexError):
            # Invalid JSONP index number
            pass

    if jsonp and jsonp_index is None:
        self.logger.warning('Invalid JSONP index number')
        r = self._bad_request()
    elif method == 'GET':
        if sid is None:
            transport = query.get('transport', ['polling'])[0]
            if transport != 'polling' and transport != 'websocket':
                self.logger.warning('Invalid transport %s', transport)
                r = self._bad_request()
            else:
                r = await self._handle_connect(environ, transport,
                                                b64, jsonp_index)
        else:
            if sid not in self.sockets:
                self.logger.warning('Invalid session %s', sid)
                r = self._bad_request()
            else:
                socket = self._get_socket(sid)
                try:
                    packets = await socket.handle_get_request(environ)
                    if isinstance(packets, list):
                        r = self._ok(packets, b64=b64,
                                        jsonp_index=jsonp_index)
                    else:
                        r = packets
                except exceptions.EngineIOError as e:
                    print("handle_request", e)
                    if sid in self.sockets:  # pragma: no cover
                        await self.disconnect(sid)
                    r = self._bad_request()
                if sid in self.sockets and self.sockets[sid].closed:
                    del self.sockets[sid]
    elif method == 'POST':
        if sid is None or sid not in self.sockets:
            self.logger.warning('Invalid session %s', sid)
            r = self._bad_request()
        else:
            socket = self._get_socket(sid)
            try:
                await socket.handle_post_request(environ)
                r = self._ok(jsonp_index=jsonp_index)
            except exceptions.EngineIOError as e:
                print("handle_request", e)
                if sid in self.sockets:  # pragma: no cover
                    await self.disconnect(sid)
                r = self._bad_request()
            except:  # pragma: no cover
                # for any other unexpected errors, we log the error
                # and keep going
                self.logger.exception('post request handler error')
                r = self._ok(jsonp_index=jsonp_index)
    elif method == 'OPTIONS':
        r = self._ok()
    else:
        self.logger.warning('Method %s not supported', method)
        r = self._method_not_found()
    if not isinstance(r, dict):
        return r
    if self.http_compression and \
            len(r['response']) >= self.compression_threshold:
        encodings = [e.split(';')[0].strip() for e in
                        environ.get('HTTP_ACCEPT_ENCODING', '').split(',')]
        for encoding in encodings:
            if encoding in self.compression_methods:
                r['response'] = \
                    getattr(self, '_' + encoding)(r['response'])
                r['headers'] += [('Content-Encoding', encoding)]
                break
    cors_headers = self._cors_headers(environ)
    make_response = self._async['make_response']
    if asyncio.iscoroutinefunction(make_response):
        response = await make_response(r['status'],
                                        r['headers'] + cors_headers,
                                        r['response'], environ)
    else:
        response = make_response(r['status'], r['headers'] + cors_headers,
                                    r['response'], environ)
    return response

sio = socketio.AsyncServer(async_mode='asgi')
ns_terminal = TerminalNamespace('/', sio)
sio.register_namespace(ns_terminal)
socket = socketio.ASGIApp(sio)
sio.eio._handle_connect = types.MethodType(_handle_connect, sio.eio)
sio.eio.handle_request = types.MethodType(handle_request, sio.eio)