import dash
from dash.dependencies import Input, Output
import dash_html_components as html
import dash_core_components as dcc

app = dash.Dash(__name__) #, server_service=dash.Services.NORMAL)


app.layout = html.Div([
    dcc.Slider(id='slider', value=5, min=0, max=100, step=1),
    html.Div(id='slider_output'),
])

@app.callback(Output('slider_output', 'children'), [Input('slider', 'value')])
#async def func(value):
def func(value):
    return value



if __name__ == '__main__':
    app.run_server(host='0.0.0.0', port=5000)