import dash
from dash.dependencies import Input, Output
import dash_html_components as html
import dash_core_components as dcc
import plotly.express as px
from threading import Timer

app = dash.Dash(__name__)

class Example:
    def __init__(self, app):
        self.app = app
        self.temp = 70
        self.data = [self.temp for i in range(20)]
        self.timer = None
        self.count = 0

        self.app.layout = html.Div([html.Div("Thermostat"),
            dcc.Slider(id='thermostat', value=self.temp, min=50, max=90, step=1, updatemode='drag',
                marks={50: '50 °F', 55: '55 °F', 60: '60 °F', 65: '65 °F', 70: '70 °F', 75: '75 °F',
                80: '80 °F', 85: '85 °F', 90: '90 °F'}),
            html.Div(id='temp'),
            dcc.Graph(id='temp_graph'), 
            html.Div('Server rebooting'),
            html.Progress(id='progress', max=10, value=str(5)),  
        ])

        @self.app.callback_shared(None, [Input('thermostat', 'value')])
        def func(value):
            self.temp = value

        self.app.clientside_callback(
            """
            function(value) {
                return value + ' °F';
            }
            """,
            Output('temp', 'children'),
            [Input('thermostat', 'value')]
        )

        @self.app.callback_connect
        def func(client, connect):
            print(client, connect, len(app.clients))    
            if connect and len(app.clients)==1:
                self.timer_callback()
            elif not connect and len(app.clients)==0:
                self.timer.cancel()

    def timer_callback(self):
        print('***', self.count)
        self.data.append(self.temp)
        self.data.pop(0)
        figure = px.line(
            dict(Time=[i for i in range(len(self.data))], Temperature=self.data),
            x='Time',
            y='Temperature',
            range_y =[49, 91]
        )
        
        self.app.push_mods({
            'temp_graph': {'figure': figure},
            'progress': {'value': str(self.count)}
        })

        self.count += 1;
        if self.count>10:
            self.count = 0
        self.timer = Timer(.5, self.timer_callback)
        self.timer.start()


if __name__ == '__main__':
    Example(app)
    app.run_server(debug=True, host='0.0.0.0', port=5000)