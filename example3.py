import dash
from dash.dependencies import Input, Output, State
import dash_html_components as html
import dash_core_components as dcc

app = dash.Dash(__name__)
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
        return Output('content', 'children', authenticated_layout)
    else:
        return Output('message', 'children', 'Incorrect (hint: username="username", password="password")'), \
            Output('password', 'value', ''), Output('username', 'value', '')

@app.callback(None, [Input('logout', 'n_clicks')])
def func(value):
    return Output('content', 'children', login_layout)

if __name__ == '__main__':
    app.run_server(debug=True, host='0.0.0.0', port=5000)