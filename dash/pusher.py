import asyncio
import json
import quart
import time

def serialize(obj):
    if hasattr(obj, 'to_plotly_json'):
        res = serialize(obj.to_plotly_json())
    elif isinstance(obj, dict):
        res = {}
        for key in obj:
            res[key] = serialize(obj[key])
    elif isinstance(obj, list) or isinstance(obj, tuple):
        res = []
        for i in obj:
            res.append(serialize(i))
    else:
        return obj
    return res


class Client:
    def __init__(self):
        self.send_queue = asyncio.Queue()
        self.connect_time = time.time()

class Pusher:

    def __init__(self, server):
        self.server = server
        self.clients = []
        self.loop = None
        self.url_map = {}

        # websocket connection handler 
        @self.server.websocket('/_push')
        async def update_component_socket():
            print('**** spawning')
            if self.loop is None:
                self.loop = asyncio.get_event_loop()
            client = Client()
            self.clients.append(client)
            socket_sender = asyncio.create_task(quart.copy_current_websocket_context(self.socket_sender)(client))
            socket_receiver = asyncio.create_task(quart.copy_current_websocket_context(self.socket_receiver)(client))
            try:
                await asyncio.gather(socket_sender, socket_receiver)
            finally:
                self.clients.remove(client)
                print('*** exitting')


    async def socket_receiver(self, client):
        print('*** ws receive')
        try:
            while True:
                data = await quart.websocket.receive()
                data = json.loads(data);
                # Create new task so we can handle more messages, keep things snappy.
                asyncio.create_task(quart.copy_current_websocket_context(self.dispatch)(data, client))
        except asyncio.CancelledError:
            raise
        finally:
            print("*** ws receive exit")


    async def socket_sender(self, client):
        print('*** ws send')
        try:
            while True:
                mod = await client.send_queue.get()
                try:
                    json_ = json.dumps(mod)
                except TypeError:
                    json_ = json.dumps(serialize(mod)) 
                await quart.websocket.send(json_)
        except asyncio.CancelledError:
            raise
        finally:
            print("*** ws send exit")


    async def dispatch(self, data, client):
        index = data['url']
        if index.startswith('/'):
            index = index[1:]

        print('*** url', index, data['id'], data['data'])
        func = self.url_map[index]
        await func(data['data'], client, data['id'])

    async def respond(self, data, request_id):
        assert request_id is not None
        data = {'id': request_id, 'data': data}
        try:
            json_ = json.dumps(data)
        except TypeError:
            json_ = json.dumps(serialize(data)) 
        await quart.websocket.send(json_)

    def add_url(self, url, callback):
        self.url_map[url] = callback

    async def send(self, id_, data, client=None, x_client=None):
        message = {'id': id_, 'data': data}

        # send by putting in event loop
        # Oddly, push_nowait doesn't get serviced right away, so we use asyncio.run_coroutine_threadsafe
        if client is None: # send to all clients
            for client in self.clients:
                if client is not x_client:
                    await client.send_queue.put(message)
        else:
            await client.send_queue.put(message)
        