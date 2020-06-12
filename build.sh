rm -rf build
git clone https://github.com/richlegrand/dash_devices.git build
cd build/dash-renderer
npm i 
npm run build:js
cd ..
mkdir build
cp LICENSE README.md build
cp setup_devices.py build/setup.py
cp MANIFEST_devices.in build/MANIFEST.in
mkdir build/dash_devices
cp -r dash/* build/dash_devices/
cp dash-renderer/dash_renderer/dash_renderer.min.js build/dash_devices
cp dash-renderer/dash_renderer/dash_renderer.dev.js build/dash_devices
cd build
python3 setup.py sdist bdist_wheel
