from glob import glob
import os
from setuptools import find_packages, setup

package_name = 'd22diffw3_v2rc_agent'

# Get the directory where setup.py is located
setup_file_dir = os.path.dirname(__file__)

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # Install the specific launch file (or all .py files in launch/)
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
    ],
    install_requires=[
        'setuptools',
    ],
    zip_safe=True,
    maintainer='robotcad',
    maintainer_email='robotcad@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'agent_node = d22diffw3_v2rc_agent.agent_node:main',
        ],
    },
)
