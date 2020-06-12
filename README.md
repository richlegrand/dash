# Dash for devices (dash_devices)

For devices seeking a front-end, [Dash](https://github.com/plotly/dash) can be used to provide an easy-to-program and browser-compatible user interface.  It's compelling!  You only need to know a bit of Python...  But there are some [issues](#problems-we-encountered-with-dash-and-devices) that need to be considered when using Dash with hardware/devices.  This fork of Dash is an attempt to address these issues.

Among other changes, we've introduced _shared_ callbacks:

![Shared callbacks](https://user-images.githubusercontent.com/913165/82079294-3cfb5d80-96a8-11ea-99be-35473d28a5f2.gif) 

Here, the shared slider and shared text box are shared across all clients, which is what you might want with a device or shared resource.  The other components are regular (not shared) to show that you can mix shared/regular components in a layout.

When a component is shared, the property changes to that component are pushed to all clients by the Dash server over WebSocket(s).  Each client gets a logically consistent and intentional display of the state of the system, device, or shared resource.  

To give it a try:

1.  Make sure you have a recent version of [Dash](https://pypi.org/project/dash/) installed.  As of this writing version 1.12.0 was the most recent version.
2.  Install quart and quart_compress:
`pip3 install quart`
`pip3 install quart_compress`
3.  Install dash_devices:
`pip3 install dash_devices`
4.  Download one of the examples (e.g. [example1.py](https://github.com/richlegrand/dash_devices/blob/dev/example1.py)) and run it (e.g. `python3 example1.py`).
5.  Point your browser to `localhost:5000`. 
6.  Repeat (5) with another browser tab.  


## Background

We had planned to launch a crowdfunding campaign for a [Raspberry Pi-based AI camera](https://www.vizycam.com/), but COVID-19 happened (it's still happening), and we hit pause on the launch of the campaign.  I eventually used some of my new free time to work on this.  Disclaimer: I'm not an expert in this space -- my main qualification is that I have a good amount of firsthand experience using Dash and Flask with devices/hardware.  And I’m a big fan of Dash.

![Vizy in Motionscope mode](https://user-images.githubusercontent.com/913165/82081637-471f5b00-96ac-11ea-9649-31bf2893512f.gif)

Dash was designed (I'm paraphrasing) to give non-expert programmers the ability to make beautiful and informative dashboards.  Along those lines, I wanted make it easier for the Raspberry Pi/maker community to put their hardware creations/ideas online for anyone to interact with.  Dash can do this out of the box, but the required [workarounds](#problems-we-encountered-with-dash-and-devices) can be a bummer...  I also wanted to make our product work better and be easier to program.  

I hope that others find these changes useful.  


## Our additions to Dash

We didn't take anything away from Dash, we just added stuff...


### Shared callbacks

We added `callback_shared()`.  For example:

```python
# Share slider value with all clients
@app.callback_shared(Output('slider_output', 'children'), [Input('slider', 'value')])
def func(value):
    # ... code to interact with hardware/device(?)
    return value
```

Declaring a callback as shared does several things:

1.  The outputs/updates are shared with all clients.
2.  The callback routine is threadsafe/serialized.
3.  The callback routine is called upon server initialization, not upon client initialization.
4.  Updates happen over WebSocket instead of HTTP requests.

Shared callbacks are useful when you want to interact with a device (all clients see the see the live state of the device and more than one client can connect.)  See [example1.py](https://github.com/richlegrand/dash_devices/blob/dev/example1.py), [example1a.py](https://github.com/richlegrand/dash_devices/blob/dev/example1a.py), [example2.py](https://github.com/richlegrand/dash_devices/blob/dev/example2.py), and [example2a.py](https://github.com/richlegrand/dash_devices/blob/dev/example2a.py). 


### Client awareness


#### app.clients 

WebSockets create an active connection between the server and the client.  The connection exists as long the browser tab is present in the browser.  It allows the server to easily maintain a table of active clients.  Your dash app can access the table of active clients through `app.clients`.  Inside the table each client has a connection time, ip address, hostname and authentication value.  See [example3.py](https://github.com/richlegrand/dash_devices/blob/dev/example3.py) and [example3a.py](https://github.com/richlegrand/dash_devices/blob/dev/example3a.py).  Since a given client has a `__dict__` attribute, you can easily add whatever fields you want.  


#### client field in callback_context

We added a `client` field to the `dash.callback_context`.  This is accessed within any callback (see [example3a.py](https://github.com/richlegrand/dash_devices/blob/dev/example3a.py).)  The `client` field is the requesting client, so the callback can do something different based on the client's authentication or some other criteria, for example.


#### callback_connect

We also added `callback_connect` for the server to communicate when a client connection changes. 

```python
@app.callback_connect
def func(client, connect):
    print(client, connect, len(app.clients))    
```

The `client` argument is the client whose connection status has changed.  The  `connect` argument is either `True` for the client connecting or `False` for the client disconnecting.  The function provided to `callback_connect` can be a coroutine. 

See [example2.py](https://github.com/richlegrand/dash_devices/blob/dev/example2.py), [example2a.py](https://github.com/richlegrand/dash_devices/blob/dev/examplesa.py), [example3.py](https://github.com/richlegrand/dash_devices/blob/dev/example3.py), and [example3a.py](https://github.com/richlegrand/dash_devices/blob/dev/example3a.py).


### push_mods()


Devices have _state_ and the state sometimes needs to be communicated to the client(s) outside of a callback.  Modifications to layout components (mods) can be sent to an individual client or all clients.  For example, to send mods to all clients:

```python
app.push_mods({'slider': {'value': val}}) // leave off the client arg
```

or send to just one client:

```python
app.push_mods({'slider': {'value': val}}, client) // specify the client
```

To modify multiple properties:

```python
app.push_mods({
    'slider': {'value': val},
    'graph': {'figure': figure},
    'out': {'style': {'display': 'none'}}
})
```

See [example2.py](https://github.com/richlegrand/dash_devices/blob/dev/example2.py) and [example2a.py](https://github.com/richlegrand/dash_devices/blob/dev/example2a.py).


### "No output" option for callbacks

Dash requires that each callback have at least one output.  But sometimes you just want a callback (no output wanted/needed.)  For example, a button to take a picture.  Here, the callback to the button exists to take the picture and store it somewhere (a side-effect), not (typically) to inject another piece of data into the layout. 

For example:

```python
@app.callback_shared(None, [Input('slider', 'value')]) # specify None for output
def func(value):
    print('Slider:', value)
    # return nothing
```

See [example1a.py](https://github.com/richlegrand/dash_devices/blob/dev/example1a.py).

You can do the same with regular (non-shared) callbacks, but our intention was to use with shared callbacks, where side-effects are sometimes the main point.  


### Alternate outputs for callbacks

Sometimes you don't want to be restricted to just one given output (or set of outputs.)  Dash has the [no_update](https://dash.plotly.com/advanced-callbacks) object that helps address this, but we found that we would eventually run into the "duplicate output" restriction (two or more callbacks can't share an output id.property.)

For example, to return an alternate result (or set of results):

```python
@app.callback(Output('content', 'children'), [Input('submit', 'n_clicks')], 
    [State('username', 'value'), State('password', 'value')])
def func(submit, username, password):
    if username=='username' and password=='password':
        return authenticated_layout # Normal output 
    else: # Alternate output
        return Output('message', 'children', 'Incorrect username and/or password!'), \
            Output('password', 'value', ''), Output('username', 'value', '')
```

See [example3.py](https://github.com/richlegrand/dash_devices/blob/dev/example3.py).

The upshot here is to use an `Output` object (or objects) to return the alternate result.  (Note, the third argument to `Output` is something we added -- it's optional, but required here to specifiy the value of the given `Output`.) 

This works with shared callbacks also.  


### Asyncio

We use [Quart](https://pgjones.gitlab.io/quart/) instead of Flask.  Quart is a really nice asyncio implementation of Flask.  Quart is typically faster than Flask, but we chose it also because it has built-in WebSocket support.  Flask works well with Flask-SocketIO (as you'd expect), but it requires that you monkey-patch the Python standard library.  This can break things.  For example, we found that monkey-patching breaks Google's Oauth 2.0 library.  Asyncio seemed like a better path forward than eventlet (what's typically used for WebSockets and the reason behind monkey-patching.)  And asyncio has been really nice to work with.  For example, sending N messages to N clients is what asyncio is really good at.  Quart requires Python 3.7 or higher, however. 

We gave callbacks the option of being coroutines (async) or synchronous routines.  Like the Flask implementation of Dash, synchronous routines execute in their own thread to keep things snappy.  Coroutines execute in their own asyncio task for the same reason.  Giving callbacks this option probably contributed to the biggest changes to the server-side code.  The callback_context code in dash.py needed significant refactoring.  

We haven't measured any noticeable performance improvement with Quart ([see below](#benchmarks).)  But we expect that Dash with Quart has better CPU scaling than with Flask.   


### Other modes/services

Shared callbacks assume that you have a single resource that you want to share.  We assumed this was the most common case, but what if you have N resources, e.g. 4 cameras or 2 robots, and you want each client to interact with a separate resource?  In this case, each client would get a different component view, but still need WebSocket service and serialized callbacks.  

We created the "S2" service with this in mind.  By using the client field in the callback_context a given callback can provide a device-specific view for a given client.  

The `Services` class in [dash.py](dash/dash.py) makes it fairly easy to customize a callback service that better meets your needs.  The custom service can be passed into `callback()`. 


## Benchmarks 

__TLDR: WebSockets are faster__, up to 3x faster than using HTTP requests for component updates.  That's the biggest takeaway here.  A Dash server that uses WebSockets for component updates is significantly faster.  So you might consider using dash_devices for no other reason than it runs significantly faster than normal Dash. 

How long does it take for a client to send a component update and receive a response from the server?  This "round-trip" time or "server latency" captures an important performance metric for the server, and it's what we measured. 


| Service           | Flask       | Quart with synchronous callback | Quart with coroutine callback |
| ----------------- | ----------- | ------------------------------- | ----------------------------- |
| HTTP service      | __16.3 ms__ | 18.7 ms                         | 18.1 ms                       |
| WebSocket service | -           | __5.6 ms__                      | __4.8 ms__                    |
 

Flask with HTTP service is what Dash uses normally.  I'm guessing that WebSockets are faster mostly because a given WebSocket connection is persistent.  The overhead of opening and closing an HTTP connection takes time.  

I was expecting to see Quart and asyncio add a small improvement in performance over Flask and traditional threading (HTTP service row).  The measurements didn't show this though (Quart was a bit slower, which was surprising.)  I should note that there were a few milliseconds of noise in the numbers, especially the HTTP service measurements.  I'd expect that Quart/asyncio would have better CPU scaling than Flask though. 

See the [notes below](#notes-about-benchmark-testing) for more details about these tests.


## Problems we encountered with Dash and devices

### The root of the problem

The root of the problem is that devices make the Dash server _state-ful_.  Dash's design avoids any kind of state on the server side.  State complicates things.  When you add state to the server:

1.  You want to communicate the state to the client(s).  In general, the state can change both because of client interactions and independent of client interactions.    
2.  You want the client(s) to modify/affect the state.

(1) can be challenging because there’s no push mechanism in Dash.  We [added (hacked) a WebSocket](https://community.plotly.com/t/triggering-callback-from-within-python/23321/6) and that worked reasonably well as long as there was a single client.  (2) isn’t a problem as long as there’s a single client.

So keeping things limited to a single client sounds like it might be a reasonable solution.  This limitation can be challenging to enforce though, and multiple clients/users/connections are usually a desirable feature.  

With shared callbacks the state changes are distributed to clients automatically, and multiple clients can safely modify the state.  


### Issues with HTTP requests and ordering 

One of the first problems we noticed with Dash was that the brightness slider on our camera would act flakey -- the brightness slider would be at 100% but the camera would only be 87% brightness because the most recent callback was 87%.  The reason for this: each slider update from the client is sent as a separate HTTP request, and the receive order, while usually correct, sometimes isn't correct -- the 100% message might be received _before_ the 87% message.  *This is the nature of HTTP requests -- the ordering isn't guaranteed.*  It isn’t a problem within the normal Dash scheme of things, it’s only a problem when the server needs messages to be delivered in order (e.g. when you are interacting with a device.)

The issue is fixed by using a WebSocket to send component updates.  The WebSocket guarantees ordering, and it's faster -- see [benchmarks](#benchmarks).


### Thread safety and callbacks 

Each Dash callback is executed in a separate thread.  This keeps the Dash server snappy:  it doesn't wait for a given callback to complete.  But when you have a shared resource being accessed in the callback (e.g. a device) the callback code is typically no longer threadsafe.  So we added a lock to each shared callback.  The WebSocket queue guarantees ordering and the lock guarantees serialization of shared callbacks.  This only applies to shared callbacks -- regular callbacks are unchanged.  

For shared callbacks, "serialization" can be disabled in the "service" argument to `callback()`. 


### Race conditions

There is a more general issue of race conditions that arises when you mix HTTP requests with WebSocket communication.  So we made all communication (layout upload, dependencies and component updates) happen over WebSocket by default, but this can be set as a config option (via `server_service`).   With all component-related communcation happening over WebSocket, messages are delivered in order, and the odd race condition is avoided. 


## Notes about benchmark testing

*  The tests were conducted on a Ubuntu 18.04 i7 machine (server).  
*  Python 3.7.7
*  The client was a Windows 10 machine, Chrome browser.
*  Network was 100BT, local.
*  For Flask tests, Dash 1.12.0, git HEAD revision count 3200
*  Flask 1.1.2
*  Quart 0.11.5

The tests were done by modifying dash_renderer -- inserting a timer in `handleServerside()`.  Start the timer before fetch, stop the timer when `data` is received, then take the time difference and insert into a running averager until the average sufficiently converges.  This usually happens after 100 or so measurements, but to keep things consistent I ran each test for 300 "clicks" (I would click on a slider object.)  The test program is [here](timer.py).

The timer code is [here](dash-renderer/src/timer.js).  For example, to insert into (unmodified) [index.js](dash-renderer/src/actions/index.js):

```javascript
function handleServerside(config, payload, hooks) {
    if (hooks.request_pre !== null) {
        hooks.request_pre(payload);
    }
    // *********** here
    const timer = startTimer();

    return fetch(
        `${urlBase(config)}_dash-update-component`,
        mergeDeepRight(config.fetch, {
            method: 'POST',
            headers: getCSRFHeader(),
            body: JSON.stringify(payload),
        })
    ).then(
        res => {
            const {status} = res;
            if (status === STATUS.OK) {
                return res.json().then(data => {
                    // *************** and here
                    stopTimer(timer);
    ...
```

We also ran tests using localhost (client and server running on the same Ubuntu machine.)  The results are below, included for fun and/or for whomever is interested:  

| Service           | Flask       | Quart with synchronous callback | Quart with coroutine callback |
| ----------------- | ----------- | ------------------------------- | ----------------------------- |
| HTTP service      | __18.0 ms__ | 19.5 ms                         | 19.0 ms                       |
| WebSocket service | -           | __5.2 ms__                      | __4.7 ms__                    |


### Other potential speed-ups

You can chain callbacks together in Dash -- the output of one callback is the input for another, the output of that callback is the input for yet another, etc.  Dash handles chaining at the client.  The client dispatches the callback at the server, gets the result and then dispatches the next callback.  The process continues until the end of the chain is reached.  Using this method, the network latency is added two times for each callback in the chain. 

For shared callbacks, callbacks are initiated and chained together at the server.  The callbacks are called in order and the outputs are sent to the client(s) over WebSocket(s) in order.  Using this method, a slow network will result in a delayed propagation of the callback results to the client(s), but no significant delays in the execution of the callbacks.    

The same can be done (I think) for non-shared (regular) callbacks for a potential a speed-up.   
