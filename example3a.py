import dash_devices
from dash_devices.dependencies import Input, Output, State
import dash_html_components as html
import dash_core_components as dcc
import time

app = dash_devices.Dash(__name__)
app.config.suppress_callback_exceptions = True


app.layout = html.Div(html.Div(id='content'), id='main')

login_layout = [
    html.Div("Username:"),
    dcc.Input(id="username", type="text", value=''), 
    html.Div("Password:"),
    dcc.Input(id="password", type="text", value=''), 
    html.Div(html.Button('Submit', id="submit")),
    html.Div(id='message')
]

authenticated_layout = [
    "You've successfully logged in!",
    html.Div(html.Button('Logout', id="logout")),
    html.Div(id='admin_content'),
]

@app.callback(Output('content', 'children'), [Input('main', 'children')])
def func(value):
    return login_layout

# Note, callbacks without outputs are not called upon unitialization,
# (which is what we want here and otherwise.)
@app.callback(None, [Input('submit', 'n_clicks')], 
    [State('username', 'value'), State('password', 'value')])
def func(submit, username, password):
    if username=='username' and password=='password':
        dash_devices.callback_context.client.authentication = 'user'
        update_admin_clients()  
        return Output('content', 'children', authenticated_layout)
    elif username=='admin' and password=='admin':
        dash_devices.callback_context.client.authentication = 'admin'
        update_admin_clients()  
        return Output('content', 'children', authenticated_layout)        
    else:
        message = [
            html.Div('(hint: username="username", password="password",'),
            html.Div('or username="admin", password="admin" for administrator access.)'),
        ]
        return Output('message', 'children', message), \
            Output('password', 'value', ''), Output('username', 'value', '')

@app.callback(None, [Input('logout', 'n_clicks')])
def func(value):
    dash_devices.callback_context.client.authentication = None
    update_admin_clients()  
    return Output('content', 'children', login_layout)

@app.callback(Output('admin_content', 'children'), [Input('main', 'children')])
def func(value):
    output = update_admin_clients(dash_devices.callback_context.client)
    if dash_devices.callback_context.client.authentication=='admin':
        return output 

def update_admin_clients(x_client=None):
    content = [html.Div("Users currently logged in:")]
    for client in app.clients:
        if client.authentication is not None:
            content.append(html.Div("{}: {} {}".format(client.authentication, client.address, time.ctime(client.connect_time))))

    for client in app.clients:
        if client==x_client:
            continue
        if client.authentication=='admin':
            app.push_mods({'admin_content': {'children': content}}, client)
    return content

@app.callback_connect
def func(client, connect):
    if not connect:
        update_admin_clients()  

if __name__ == '__main__':
    app.run_server(debug=True, host='0.0.0.0', port=5000)