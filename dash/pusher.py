import asyncio
import json
import quart
import plotly
from contextvars import ContextVar
import time
import sys
import inspect
import traceback 

context_rcount = ContextVar('context_rcount')

# This class defers the creation of the asyncio Lock until it's needed,
# to prevent issues with creating the lock before the event loop is running.
# It also handles reentrance/recursion. 
class Alock:
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
        return self.lock

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.release()


class Client(object):
    def __init__(self):
        self.send_queue = asyncio.Queue()
        self.connect_time = time.time()
        self.address = quart.websocket.remote_addr
        self.host = quart.websocket.host
        self.origin = quart.websocket.origin
        self.authentication = None

    def __str__(self):
        return "<Client: address={}, host={}, authentication={}>".format(
            self.address, self.host, self.authentication)


def exception_handler(loop, context):
    if 'future' in context:
        task = context['future']
        task.print_stack()


class Pusher(object):

    def __init__(self, server):
        self.server = server
        self.clients = []
        self.loop = None
        self.url_map = {}
        self.connect_callback = None

        # websocket connection handler 
        @self.server.websocket('/_push')
        async def update_component_socket():
            #print('**** spawning')
            try:
                # Quart creates the event loop.  This is the best place to grab it (I think).
                if self.loop is None:
                    self.loop = asyncio.get_event_loop()
                    self.loop.set_exception_handler(exception_handler)

                tasks = []
                client = Client()
                self.clients.append(client)

                if self.connect_callback is not None:
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
                if self.connect_callback is not None:
                    try:
                        await self.call_connect_callback(client, False)
                    except:
                        # Print traceback because Quart seems to be catching everything in this context.
                        traceback.print_exc()
                #print('*** exitting')

    async def call_connect_callback(self, client, connect):
        if inspect.iscoroutinefunction(self.connect_callback):
            await self.connect_callback(client, connect)
        else:
            await self.loop.run_in_executor(None, self.connect_callback, client, connect) 

    async def socket_receiver(self, client):
        while True:
            data = await quart.websocket.receive()
            data = json.loads(data)
            # Create new task so we can handle more messages and keep things snappy.
            asyncio.create_task(quart.copy_current_websocket_context(self.dispatch)(data, client))

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
                if client is not x_client:
                    await client.send_queue.put(message)
        # Send to one client.
        else:
            await client.send_queue.put(message)
        # Give caller feedback regarding failed sends.
        return result

    def callback_connect(self, func):
        self.connect_callback = func

