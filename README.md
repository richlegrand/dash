## Dash for devices

Hardware, or devices... something physical.  When you have [Dash](https://github.com/plotly/dash) interacting with a physical device things can break.  Here’s an attempt to solve some of the problems that we encountered when using Dash with devices/hardware.  

Among other changes, we've introduced "shared" callbacks:

![Shared callbacks](https://user-images.githubusercontent.com/913165/82079294-3cfb5d80-96a8-11ea-99be-35473d28a5f2.gif) 

Here, the shared slider and shared text box are shared across all clients, which is what you might want with a device.  The other components are regular (not shared) to show that you can mix shared/regular components in a layout.

When a component is shared, the property changes to that component are pushed to all clients by the Dash server over websocket(s).  Each client gets a logically consistent and intentional display of the state of the system.  

Download the [tarfile](https://github.com/charmedlabs/vizy/files/4636133/dash_devices.tar.gz) to give it a try:

1.  Untar, uncompress the tar file.
2.  Go into dash directory and run one the examples (e.g. "python3 example1.py").  It assumes that you have a recent version of Dash already installed.  (Note, running the examples in this directory won't modify your existing dash installation.)
3.  Point your browser to localhost:5000. 

### Background

We had planned to crowdfund a [Raspberry Pi-based AI camera](https://www.vizycam.com/), but COVID happened and we hit pause on the launch.  So I decided to take some of my new free time to work on this.  Disclaimer: I'm not an expert in this space -- my main qualification is that I have a good amount of firsthand experience using Dash and Flask with devices/hardware.  And I’m a big fan of Dash.

Dash was designed (I'm paraphrasing) to give non-expert programmers the ability to make beautiful and informative dashboards.  Along those lines, I wanted make it easier for the Raspberry Pi/maker community to put their physical hardware creations/ideas online for anyone to interact with.  Dash can do this out of the box, but the required workarounds can be a bummer...  I also wanted our product work better.


![Vizy in Motionscope mode](https://user-images.githubusercontent.com/913165/82081637-471f5b00-96ac-11ea-9649-31bf2893512f.gif)


### Our changes to Dash

#### Shared callbacks

We added callback_shared().  For example:

```python
@app.callback_shared(Output('slider_output', 'children'), [Input('slider', 'value')])
def func(value):
    return value
```

Declaring a callback as shared does several things:

1. The outputs/updates are shared with all clients.
2. The callback routine is threadsafe/serialized.
3. The callback routine is called upon server initialization, not upon client initialization.
4. The updates happen over websocket instead of HTTP requests.

Shared callbacks are useful when you want to interact with a device.  See [example1.py](example1.py) and [example1a.py](example1a.py). 


#### Client awareness

Websockets create an active connection between the server and each client.  The connection exists as long the browser tab is present in the browser.  It allows us to maintain a table of active clients.  Your dash app can access the table of active clients in dash.clients (usually app.clients).  Inside the table each client has a connection time, ip address, hostname and authentication value.  See [example3.py](example3.py) and [example3a.py](example3a.py).  Since a given client has a \__dict__ attribute, you can also add whatever fields you want.  

Additionally, we added a "client" field to the dash.callback_context.  This is accessed within a callback (see example3a.py.)  The client field is the requesting client, so the callback can do something different based on the client's authentication or some other criteria.


#### push_mods()


Devices have "state" and the state needs to be communicated to the client(s).  push_mods can be sent to an individual client or all clients.  For example, to send to all clients:

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
    'out' {'style': {'display': 'none'}}
})
```

See [example2.py](example2.py) and [example2a.py](example2a.py).


#### No output option for callbacks

Dash requires that each callback have at least one output.  But sometimes you just want a callback (no output wanted/needed.)  For example, a button to take a picture.  Here, the callback to the button exists to take the picture and store it somewhere (a side-effect), not (typically) to inject another piece of data into the layout. 

For example:

```python
@app.callback_shared(None, [Input('slider', 'value')])
def func(value):
    print('Slider:', value)
```

See [example1a.py](example1a.py).

You can do the same with regular (non-shared) callbacks, but our intention was to use with shared callbacks, where side-effects are sometimes the main point.  


#### Alternate outputs for callbacks

Sometimes you don't want to be restricted to just one given output (or set of outputs.)  Dash has the [no_update object](https://dash.plotly.com/advanced-callbacks) that helps address this, but we would eventually run into the "duplicate output" restriction (two or more callbacks can't share an output id.property.)

For example, to return an alternate result (or set of results):

```python
@app.callback(Output('content', 'children', [Input('submit', 'n_clicks')], 
    [State('username', 'value'), State('password', 'value')])
def func(submit, username, password):
    if username=='username' and password=='password':
        return authenticated_layout
    else:
        return Output('message', 'children', 'Incorrect'), \
            Output('password', 'value', ''), Output('username', 'value', '')
```

See [example3.py](example3.py).

The upshot here is to use an Output object (or objects) to return the alternate result.  

This works with shared callbacks also.  


#### Asyncio

We use [Quart](https://pgjones.gitlab.io/quart/) instead of Flask.  Quart is a really nice asyncio implementation of Flask.  Quart is typically faster than Flask, but we chose it also because it has built-in websocket facilities.  Flask works well with Flask-SocketIO (of course), but it requires that you monkey-patch the Python standard library.  This can break things.  For example, we found that monkey-patching breaks Google's oauth2 library.  Asyncio seemed like a better path forward than gevent (what Flask typically uses and the main reason behind monkey-patching.)  

Asyncio has been really nice to work with.  For example, sending N messages to N clients is what asyncio is really good at.  Quart requires Python 3.7 or higher though. 

We gave callbacks the option of being coroutines or regular routines.  Like the Flask implementation of Dash, regular routines execute in their own thread to keep things snappy.  Coroutines execute in their own asyncio task.  Giving callbacks this option probably contributed to the biggest changes to the server-side code.  We had to more or less rewrite the callback_context sections.  

We haven't measured any noticeable performance improvement with Quart ([see below](### Benchmarks).)  But we expect that Dash with Quart has better CPU scaling than with Flask.   


#### Other modes/services

Shared callbacks assume that you have a single resource that you want to share.  We assumed this was the most common case, but what if you have N resources, e.g. 4 cameras or 2 robots, and you want each client to interact with a separate resource?  In this case, each client would get a different component view, but still need websocket service and serialized callbacks.  

We created the "S2" service with this in mind.  By using the client field in the callback_context a given callback can provide a device-specific view for a given client.  

The Services class in [dash.py](dash/dash.py) makes it possible to customize a callback service that better meets your needs.  The custom service can be passed into callback(). 



### Benchmarks 

TLDR -- websockets are faster, about 5x faster than using HTTP requests for component updates.  That's the biggest takeaway here:  a Dash server that uses websockets for component updates is significantly faster.

How long does it take for a client to send a component update and receive a response from the server?  This "round-trip" time or "server latency" captures an important performance metric for the server, and it's what we measured. 


Service | Flask | Quart w/no coroutine callback | Quart w/coroutine callback
--------|-------|-----------------------------|
HTTP service | __32 ms__ | 32 ms | 35 ms
Websocket service | - |  __6.5 ms__ | __6 ms__


I haven't dug into this, but I'm guessing that websockets are faster because a given websocket connection is persistent.  The added overhead of opening and closing a connection makes HTTP requests significantly slower (my guess.)  Fetching resources is what HTTP is really good at.  Component updates are better-suited for websocket communication it seems. 

I was expecting to see Quart and asyncio add a small improvement in performance.  The measurements didn't show this.  I should note that there were a few milliseconds of noise in the numbers, especially the HTTP service measurements.  Any improvements from Quart/asyncio were "in the noise" so to speak.  I would expect that Quart/asyncio would have better CPU scaling than Flask.  This could potentially be another good test.  

See [notes below](### Notes about performance testing) for more information about implementation, etc.


### Additional notes (you're still reading?)

#### The root of the problem

The root of the problem is that devices make the Dash server state-ful.  Dash's design avoids any kind of state on the server side.  State complicates things.  When you add state to the server:

1.  You want to communicate the state to the client(s).  In general, the state can change both because of client interactions and independent of client interactions.    
2.  You want the client(s) to modify/affect the state.

(1) can be challenging because there’s no push mechanism.  We’ve [played with adding a websocket](https://community.plotly.com/t/triggering-callback-from-within-python/23321/6) and that has worked fine for the most part as long as there’s a single client.  (2) isn’t a problem as long as there’s a single client... 

So a single client sounds like it might be a reasonable solution -- but enforcing this condition can be challenging, and multiple clients/users/connections are usually a desirable feature.  We think shared callbacks are a good solution.  The state changes are distributed to clients automatically, and clients can safely modify the state.  


#### Issues with HTTP requests and ordering 

One of the first problems we noticed with Dash was that the brightness slider on our camera would act flakey -- the brightness slider would be at 100% but the camera would only be 87% brightness because the most recent callback was 87%.  The reason for this: each slider update from the client is sent as a separate HTTP request, and the receive order, while usually correct, sometimes breaks -- the 100% message would be received before the 87% message.  This is the nature of HTTP requests.  It isn’t a problem within the normal Dash scheme of things.  It’s only a problem when the server needs messages to be delivered in order (e.g. when you are interacting with a device.)

The issue is fixed by using a websocket to send component updates.  The websocket guarantees ordering, and it's faster -- see benchmarks below.


#### Thread safety and callbacks 

Each Dash callback is executed in a separate thread.  This keeps the Dash server snappy:  it doesn't wait for a given callback to complete.  But when you have a shared resource being accessed in the callback (e.g. a device) the callback code is typically no longer threadsafe.  So we added a lock to each shared callback.  The websocket queue guarantees ordering and the lock guarantees serialization of shared callbacks.  This only applies to shared callbacks -- normal callbacks are unchanged.  For shared callbacks, "serialization" can be disabled in the "service" argument to dash.callback(). 

There is a more general issue of race conditions that arises when you mix HTTP requests with websocket communication.  So we made all communication (graph upload, dependencies and component updates) happen over websocket by default, but this is optional -- see server_service.   Messages are delivered in order to/from client and server and the odd race condition is avoided. 


### Notes about performance testing

The tests were done by modifying dash_renderer -- inserting a timer in handleServerside.  Start the timer before fetch, stop the timer when "data" is received, then take the time difference and insert into a running averager until the average sufficiently converges.  This usually happens after 100 or so measurements, but to keep things consistent I ran each test for 300 "clicks" (I would click on a slider object.)

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
