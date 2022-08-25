import logging
import os
from math import cos, sin

from py_experimenter.experimenter import PyExperimenter
from py_experimenter.result_processor import ResultProcessor


def own_function(parameters: dict, result_processor: ResultProcessor, custom_config: dict):
    # run the experiment with the given value for the sin and cos function
    sin_result = sin(parameters['value'])**parameters['exponent']
    cos_result = cos(parameters['value'])**parameters['exponent']

    # write result in dict with the resultfield as key
    result = {'sin': sin_result, 'cos': cos_result}

    # send result to to the database
    result_processor.process_results(result)


logging.basicConfig(level=logging.INFO)

# Create sqlite experimenter with name: 'name1',
# Add 6 experiments to the database.
# Execute one of the experiments.
experimenter = PyExperimenter(config_path=os.path.join('examples', 'example_use_name.cfg'), experimenter_name='name1')
experimenter.fill_table_with_rows(
    rows=[
        {'value': 1, 'exponent': 1},
        {'value': 1, 'exponent': 3},
        {'value': 2, 'exponent': 2},
        {'value': 3, 'exponent': 3},
        {'value': 8, 'exponent': 2},
        {'value': 9, 'exponent': 3},
    ]
)
experimenter.execute(own_function, 1)

# Create new experimenter with name: 'name2'
# Execute two of the experiments.
experimenter = PyExperimenter(config_path=os.path.join('examples', 'example_use_name.cfg'), experimenter_name='name2')
experimenter.execute(own_function, 2)