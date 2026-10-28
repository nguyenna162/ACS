from setuptools import find_packages, setup

package_name = 'mqtt_ros_bridge'

setup(
    name=package_name,
    version='2.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]) if False else (),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools', 'paho-mqtt>=2.0.0'],
    zip_safe=True,
    maintainer='SEVT Robotics Team',
    maintainer_email='dev@sevt-robotics.vn',
    description='VDA 5050 v2.0 MQTT to ROS 2 Nav2 bridge node',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'bridge_node = mqtt_ros_bridge.bridge_node:main'
        ],
    },
)
