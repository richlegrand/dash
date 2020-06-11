import dash_devices
from dash_devices.dependencies import Input, Output
import dash_html_components as html
import dash_core_components as dcc

app = dash_devices.Dash(__name__)
app.config.suppress_callback_exceptions = True


app.layout = html.Div([
    html.Div("Shared slider no output"),
    dcc.Slider(id='shared_slider_no_output', value=5, min=0, max=10, step=1, updatemode='drag'),

    html.Div("Shared slider with output"),
    dcc.Slider(id='shared_slider_output', value=5, min=0, max=10, step=1, updatemode='drag'),
    html.Div(id='shared_slider_output_output'),

    html.Div("Regular slider"),
    dcc.Slider(id='regular_slider', value=5, min=0, max=10, step=1, updatemode='drag'),
    html.Div(id='regular_slider_output'),

    html.Div("Shared input no output"),
    dcc.Input(id="shared_input_no_output", type="text", value=''), 

    html.Div("Shared input with output"),
    dcc.Input(id="shared_input_output", type="text", value=''), 
    html.Div(id='shared_input_output_output'),

    html.Div("Regular input"),
    dcc.Input(id="regular_input", type="text", value=''), 
    html.Div(id='regular_input_output'),
])


@app.callback_shared(None, [Input('shared_slider_no_output', 'value')])
def func(value):
    print('Shared slider no output', value)

@app.callback_shared(Output('shared_slider_output_output', 'children'), [Input('shared_slider_output', 'value')])
def func(value):
    return value

@app.callback(Output('regular_slider_output', 'children'), [Input('regular_slider', 'value')])
def func(value):
    return value

@app.callback_shared(None, [Input('shared_input_no_output', 'value')])
def func(value):
    print('Shared input no output', value)

@app.callback_shared(Output('shared_input_output_output', 'children'), [Input('shared_input_output', 'value')])
def func(value):
    return value

@app.callback(Output('regular_input_output', 'children'), [Input('regular_input', 'value')])
def func(value):
    return value


if __name__ == '__main__':
    app.run_server(debug=True, host='0.0.0.0', port=5000)