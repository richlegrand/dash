import asyncio
import json
import quart
import plotly
from contextvars import ContextVar
import time
import sys
import inspect
import traceback 
from threading import Condition
from quart import session

context_rcount = ContextVar('context_rcount')

# This class defers the creation of the asyncio Lock until it's needed,
# to prevent issues with creating the lock before the event loop is running.
# It also handles reentrance/recursion. 
class ARLock:
    def __init__(self):
        self.lock = None

    async def acquire(self):
        if self.lock is None:
            self.lock = asyncio.Lock()
        try: 
            rcount = context_rcount.get()
        except LookupError:    
            # This is the first acquisition in this context, so get lock, set count
            await self.lock.acquire()
            context_rcount.set(1)
            return 
        if rcount==0:
            # We've locked and unlocked within this context already. We need to reacquire lock.
            await self.lock.acquire()

        context_rcount.set(rcount + 1)

    def release(self):
        rcount = context_rcount.get() - 1
        context_rcount.set(rcount)
        if rcount==0:
            self.lock.release()

    def locked(self):
        if self.lock is None:
            return False
        return self.lock.locked()

    async def __aenter__(self):
        await self.acquire()
        return None

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.release()

class LockContext:
    def __init__(self):
        self.count = 0

class ARCLock:
    def __init__(self):
        self.lock = None
        self.context = None
        self.default_context = LockContext()

    async def acquire(self, context=None):
        if self.lock is None:
            self.lock = asyncio.Lock()
        if context is None:
            context = self.default_context
        if self.context is None:
            self.context = context
        if self.context.count==0 or self.context is not context:
            await self.lock.acquire()
            self.context = context

        self.context.count += 1

    def release(self):
        self.context.count -= 1
        if self.context.count==0:
            # Reset context so we don't reuse if context isn't supplied.
            self.context = None 
            self.lock.release()

    def locked(self):
        if self.lock is None:
            return False
        return self.lock.locked()


# The "most recent" locks prevent more than 1 thread or task from waiting.
# If you try to acquire the lock, the waiting thread is released with a  
# False result and the new thread starts waiting.  This prevents the "wind-up"
# that can happen when several threads are waiting on a slow-to-be-released lock.
class ALockMostRecent:
    
    def __init__(self):
        self.cond = None
        self.locked = False

    async def acquire(self):
        if self.cond is None:
            self.cond = asyncio.Condition() # Defer creation until used.
        async with self.cond:
            if not self.locked:
                self.locked = True
                return True
            
            self.cond.notify()
                
            await self.cond.wait()
            result = not self.locked
            self.locked = True
            return result
            
    async def release(self):
        async with self.cond:
            self.locked = False
            self.cond.notify()
    

class LockMostRecent:
    
    def __init__(self):
        self.cond = Condition()
        self.locked = False

    def acquire(self):
        with self.cond:
            if not self.locked:
                self.locked = True
                return True
            
            self.cond.notify()
                
            self.cond.wait()
            result = not self.locked
            self.locked = True
            return result
            
    def release(self):
        with self.cond:
            self.locked = False
            self.cond.notify()
    

class Client(object):
    def __init__(self, authentication=None):
        self.send_queue = asyncio.Queue()
        self.connect_time = time.time()
        self.address = quart.websocket.remote_addr
        self.host = quart.websocket.host
        self.origin = quart.websocket.origin
        self.authentication = authentication
        self.context = LockContext()

    def __str__(self):
        return "<Client: address={}, host={}, authentication={}>".format(
            self.address, self.host, self.authentication)


class Pusher(object):

    def __init__(self, server):
        self.server = server
        self.clients = []
        self.loop = asyncio.get_event_loop()
        self.url_map = {}
        self.connect_callback = []

        # websocket connection handler 
        @self.server.websocket('/_push')
        async def update_component_socket(authentication=None):
            #print('**** spawning')
            try:
                tasks = []
                client = Client(authentication)
                self.clients.append(client)

                if self.connect_callback:
                    tasks.append(asyncio.create_task(self.call_connect_callback(client, True)))

                tasks.append(asyncio.create_task(quart.copy_current_websocket_context(self.socket_sender)(client)))
                tasks.append(asyncio.create_task(quart.copy_current_websocket_context(self.socket_receiver)(client)))

                await asyncio.gather(*tasks)

            except asyncio.CancelledError:
                pass
            except:
                # Print traceback because Quart seems to be catching everything in this context.
                traceback.print_exc() 
            finally:
                self.clients.remove(client)
                if self.connect_callback:
                    try:
                        await self.call_connect_callback(client, False)
                    except:
                        # Print traceback because Quart seems to be catching everything in this context.
                        traceback.print_exc()
                #print('*** exitting')

    async def call_connect_callback(self, client, connect):
        for callback in self.connect_callback:
            if inspect.iscoroutinefunction(callback):
                await callback(client, connect)
            else:
                await self.loop.run_in_executor(None, callback, client, connect) 

    async def socket_receiver(self, client):
        while True:
            data = await quart.websocket.receive()
            data = json.loads(data)
            # Create new task so we can handle more messages and keep things snappy.
            task = asyncio.create_task(self.dispatch(data, client))

    async def socket_sender(self, client):
        while True:
            mod = await client.send_queue.get()
            json_ = json.dumps(mod, cls=plotly.utils.PlotlyJSONEncoder)
            await quart.websocket.send(json_)

    async def dispatch(self, data, client):
        index = data['url']
        if index.startswith('/'):
            index = index[1:]

        #print('*** url', index, data['id'], data['data'])
        func = self.url_map[index]
        await func(data['data'], client, data['id'])

    async def respond(self, data, request_id):
        assert request_id is not None
        data = {'id': request_id, 'data': data}
        json_ = json.dumps(data, cls=plotly.utils.PlotlyJSONEncoder)
        await quart.websocket.send(json_)

    def add_url(self, url, callback):
        self.url_map[url] = callback

    async def send(self, id_, data, client=None, x_client=None):
        result = 0
        message = {'id': id_, 'data': data}

        # Send to all clients.
        if client is None: 
            for client in self.clients:
                # Authentication is None basically means there's no login.
                # Otherwise authentication should evaluate to True or non-zero. 
                # This mostly prevents unauthorized clients from receiving 
                # shared mods (e.g. share_shared_mods)
                if client is not x_client and (client.authentication is None or client.authentication):
                    await client.send_queue.put(message)
        # Send to one client.
        else:
            await client.send_queue.put(message)
        # Give caller feedback regarding failed sends.
        return result

    def callback_connect(self, func):
        self.connect_callback.append(func)

