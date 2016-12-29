##########################################################################
## Summary
##########################################################################

'''
Creates flat table of decisions from our Postgres database and runs the prediction pipeline.
Starting point for running our models.
'''


##########################################################################
## Imports & Configuration
##########################################################################
import logging
import numpy, pandas
from sklearn import metrics

#Configure logging. See /logs/example-logging.py for examples of how to use this.
logging_filename = "../logs/pipeline.log"
logging.basicConfig(filename=logging_filename, level=logging.DEBUG)
#Pushes everything from the logger to the command line output as well.
logging.getLogger().addHandler(logging.StreamHandler())

#Allow modules to import each other at parallel file structure (TODO clean up this configuration in a refactor, it's messy...)
from inspect import getsourcefile
import os.path, sys
current_path = os.path.abspath(getsourcefile(lambda:0))
current_dir = os.path.dirname(current_path)
parent_dir = current_dir[:current_dir.rfind(os.path.sep)]
repo_dir = parent_dir[:parent_dir.rfind(os.path.sep)]
sys.path.insert(0, parent_dir)

import database_management



##########################################################################
## Classes
##########################################################################


class ManyModels:
    '''
    A wrapper class for training multiple sklearn models on a single dataset
    The wrapper contains:
        -The models themselves (fitted or not), passed as a dictionary from the calling function
        -X and y arrays of training data.
        -an X_test set of testing data
        -The predicted answers of all models, stored as a dataframe with rows matching the X_test dataset

    Not optimized for memory use - instead it is designed for as much flexibility and access to source data,
    models, and prediction performance as possible for use in a learning context.
    '''

    def __init__(self):

        self.models = {}  #dict of 'modelname':sklearn.model_instance
        self.X = numpy.array([[],[]]) #blank 2-d array
        self.y = numpy.array([]) #blank 1-d array
        self.answers = pandas.DataFrame() #Pandas dataframe where each row is a row of the test dataset, each column is a different model_list
        self.scores = {} #Nested dictionary of shape {'modelname': {'precision': #, 'recall': #, 'accuracy': #, 'f1': # }}

        self.X_test = None
        self.y_test = None

    #@property lets us add additional logic to the getters and setters for the X_test property (e.g., resetting the answers and scores)
    @property
    def X_test(self):
        return self.__X_test
    @X_test.setter
    def X_test(self, X_test=None):
        self.__X_test = X_test
        #reset since rows will no longer match
        self.answers = pandas.DataFrame()
        self.scores = {}

    @property
    def y_test(self):
        return self.__y_test
    @y_test.setter
    def y_test(self, y_test=None):
        self.__y_test = y_test
        #reset since rows will no longer match
        self.answers = pandas.DataFrame()
        self.scores = {}

    def fit(self, model_list=None):
        model_list = self.clean_model_list(model_list)

        for key in model_list:
            self.models[key].fit(self.X, self.y)
            print("  fitted model: " + key)

        return self

    def predict(self, model_list=None):
        model_list = self.clean_model_list(model_list)

        for key in model_list:
            self.answers[key] = self.models[key].predict(self.X_test)

            self.scores[key] = { }
            if self.y_test is not None:
                self.scores[key]['precision'] = metrics.precision_score(y_true = self.y_test, y_pred = self.answers[key].as_matrix(), average="weighted")
                self.scores[key]['recall'] = metrics.recall_score(y_true = self.y_test, y_pred=self.answers[key], average="weighted")
                self.scores[key]['accuracy'] = metrics.accuracy_score(y_true = self.y_test, y_pred=self.answers[key])
                self.scores[key]['f1'] = metrics.f1_score(y_true = self.y_test, y_pred=self.answers[key], average="weighted")

        return self.answers

    def clean_model_list(self, model_list):
            #Resolve defaults and turn a single string into a list
            if model_list is None:
                model_list = list(self.models.keys())

            if isinstance(model_list, str):
                model_list = [model_list]

            if isinstance(model_list, list):
                return model_list
            else:
                raise ValueError('A provided model_list must be a list or a string.')



##########################################################################
## Functions
##########################################################################

def run_simple_query():
    #Connect to the database
    database_connection = database_management.get_database_connection('database')
    query_result = database_connection.execute("select snapshot_id, table_name from manifest where snapshot_id='c2005-07'")
    for query_row in query_result:
        print(query_row['snapshot_id'] + " | " + query_row['table_name'])

def get_decisions_table(equal_split = False):
    '''
    Queries the database to get our decisions table for training/testing purposes
    '''

    logging.info("Getting the data from the database...")
    #Connect to the database
    database_connection = database_management.get_database_connection('database')
    query_result = database_connection.execute("select snapshot_id, table_name from manifest where snapshot_id='c2005-07'")

    # Open and read the SQL command file as a single buffer
    query_path = parent_dir + "\wrangling\decisions_partial_churn_filter.sql"
    fd = open(query_path, 'r')
    sqlFile = fd.read()
    fd.close()

    #This query will be built on and/or replaced once we get Kashif's SQL query working
    query_text = "select" + """
                        temp.decision

                        , rent.hd01_vd01 as median_rent

                        /*, lag(c.contract_term_months_qty,1) over (partition by c.contract_number order by c.snapshot_id) term_mths_lag*/
                        , c.contract_term_months_qty
                        , c.assisted_units_count

                        , c.is_hud_administered_ind
                        , c.program_type_group_name

                        , c.rent_to_FMR_ratio
                        , c."0br_count" br0_count
                        , c."1br_count" br1_count
                        , c."2br_count" br2_count
                        , c."3br_count" br3_count
                        , c."4br_count" br4_count
                        , c."5plusbr_count" br5_count
                        """ + "from (" + sqlFile +  """
                                ) as temp
                inner join contracts as c
                on c.contract_number = temp.contract_number and c.snapshot_id = temp.snapshot_id
                inner join geocode as g
                on c.property_id = g.property_id
                inner join acs_rent_median as rent
                on g.geoid::text = rent.geo_id2::text

                where churn_flag<>'churn'
                """
    both_in_out = " and decision in ('in', 'out')"
    just_in = " and decision in ('in')"
    just_out = " and decision in ('out')"
    if equal_split == False:
        query1 = query_text + both_in_out
        query_dataframe = pandas.read_sql(query1, database_connection)

    #Run the query twice to get an equal amount of ins and outs
    else:
        out_query = query_text + just_out
        out_dataframe = pandas.read_sql(out_query, database_connection)

        #There are more In decisions, so the size of the out_dataframe is the limiting factor
        in_limit = len(out_dataframe.index)
        in_query = query_text + just_in + "LIMIT {}".format(in_limit)
        in_dataframe = pandas.read_sql(in_query, database_connection)

        query_dataframe = pandas.concat([in_dataframe, out_dataframe], ignore_index = True)

    return query_dataframe

def get_custom_pipeline():
    '''
    Defines the pipeline needed to transform our data after it has been cleaned by the clean_dataframe method
    '''

    logging.info("Getting a custom pipeline...")
    from sklearn.preprocessing import StandardScaler, Imputer, LabelEncoder, MinMaxScaler, OneHotEncoder
    from sklearn.pipeline import Pipeline

    #TODO figure out how to use column names instead of numbers (categorical_features = [4] represents 5th column i.e. program_type_group_name)
    pipeline = Pipeline([   ('imputer', Imputer())
                            ,('onehot', OneHotEncoder(categorical_features=[4], sparse=False))
                            ])

    return pipeline


def clean_dataframe(dataframe, debug=False):
    '''
    This method takes and returns a dataframe, which is the training data from our database.
    The scope of this function is to get the data ready for sklearn's encoders, using
    custom functions for each variable that needs transformation.

    Examples:
    -Manual categorical encoding
    -Conversion of placeholder nulls (e.g. 'N' or '-') to appropriate null values
    -Manual imputation when needed (e.g. converting 2000+ value of median rent to 2000)


    All the code in this section is custom tailored to the peculiarities of the data formats in our data
    '''

    logging.info("Cleaning the data...")

    if debug == True:
        dataframe.to_csv('before.csv')

    #categorical encoding - method #1
    decision_mapping = {'in': 1, 'out': 0}
    dataframe['decision'] = dataframe['decision'].map(decision_mapping)
    is_mapping = {'Y': 1, 'N': 0} #used for any field that starts with is_
    dataframe['is_hud_administered_ind'] = dataframe['is_hud_administered_ind'].map(is_mapping)

    #method #2
    replace_mapping = { 'median_rent': {'-': numpy.nan, '2,000+': 2000}}
    dataframe.replace(to_replace=replace_mapping, inplace=True)

    #method #3
    from sklearn.preprocessing import LabelEncoder
    label_encoder_program_name = LabelEncoder()
    dataframe['program_type_group_name'] = label_encoder_program_name.fit_transform(dataframe['program_type_group_name'])

    if debug == True:
        dataframe.to_csv('after.csv')

    return dataframe



if __name__ == '__main__':
    dataframe = get_decisions_table(equal_split = True)
    dataframe = clean_dataframe(dataframe, debug=True)