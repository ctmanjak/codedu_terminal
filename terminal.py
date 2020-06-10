import subprocess
import socketio
import pty
import select
import os
import sys
import signal
import asyncio

signal.signal(signal.SIGCHLD, lambda signum, bt: os.waitpid(-1, os.WNOHANG))

class TerminalNamespace(socketio.AsyncNamespace):
    def __init__(self, namespace, sio):
        super().__init__(namespace)
        self.sio = sio
        self.ext = {"python3":".py", "clang":".c"}

    
    async def on_connect(self, sid, environ):
        print('hi')
        await self.sio.emit('test', {'data': 'hi'}, to=sid)

    
    async def on_disconnect(self, sid):
        session = await self.sio.get_session(sid)
        print("disconnect")
        if session:
            os.write(session['fd'], "exit\r\n".encode())
            os.kill(session['child_pid'], signal.SIGKILL)
    
    
    async def wait_for_idle(self, session_id, caller, **kargs):
        try:
            session = await self.sio.get_session(session_id)
        except KeyError:
            return

        while not session['idle']:
            print('wait_for_idle', caller.__name__)
            session = await self.sio.get_session(session_id)
            await asyncio.sleep(0.5)

        session['idle'] = False
        await self.sio.save_session(session_id, session)

        await caller(**kargs)

        session['idle'] = True
        await self.sio.save_session(session_id, session)

    
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
        session = await self.sio.get_session(sid)

        if session:
            return

        (child_pid, fd) = pty.fork()

        if child_pid == 0:
            subprocess.run(["docker", "run", "-it", "--rm", "--name", sid, "ctmanjak/codedu_base"])
        else:
            print("opening a new session")

            await self.sio.save_session(sid, {"idle":None,"fd":fd, "child_pid":child_pid})

            print("connect: child pid is", child_pid)

            self.sio.start_background_task(
                target=self.read_output, sid=sid
            )
            
            print("connect: task started")

    
    async def on_client_input(self, sid, data):
        session = await self.sio.get_session(sid)
        
        if session:
            file_desc = session["fd"]
            if file_desc:
                os.write(file_desc, f"{data['input']}".encode())
                await self.sio.save_session(sid, session)
                        
    
    async def read_output(self, sid):
        max_read_bytes = 1024 * 2
        
        while True:
            try:
                session = await self.sio.get_session(sid)
            except KeyError:
                break

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
                        try:
                            output = os.read(file_desc, max_read_bytes).decode()
                            if output:
                                await self.emit_lock(
                                    "client_output",
                                    {
                                        "output": output,
                                    },
                                    sid,
                                )
                        except OSError:
                            await self.sio.disconnect(sid)
                            sys.exit(0)

    
    async def _emit_lock(self, event, data, sid):
        await self.sio.emit(
                event,
                data,
                to=sid,
            )

    
    async def emit_lock(self, event, data, sid):
        await self.wait_for_idle(sid, self._emit_lock, event=event, data=data, sid=sid)

sio = socketio.AsyncServer(async_mode='asgi')
sio.register_namespace(TerminalNamespace('/', sio))
socket = socketio.ASGIApp(sio)