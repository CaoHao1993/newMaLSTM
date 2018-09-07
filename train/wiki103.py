# # CODE

from time import time
import pandas as pd
import pickle
import numpy as np
from gensim.models import KeyedVectors
import re
from nltk.corpus import stopwords
from sklearn.model_selection import train_test_split
import torch
from torch.autograd import Variable
import collections
import itertools
import datetime
import keras

from keras.preprocessing.sequence import pad_sequences
from keras.models import Model
from keras.layers import Input, Embedding, LSTM, Merge, Dense, Flatten, Activation, Bidirectional, RepeatVector, Permute, Lambda, TimeDistributed
from keras.layers.merge import multiply, concatenate
from ManDist import ManDist
import keras.backend as K
#from keras.optimizers import Adadelta
from keras.callbacks import ModelCheckpoint, CSVLogger

from property import Property
from property import save_property


# Global variables

# File paths
TRAIN_CSV = '/home/ch/data/Quora.csv'
EMBEDDING_FILE = '/home/ch/data/fwd_wt103.h5'

MODEL_SAVING_DIR = 'models/'
PR_PATH = '/home/ch/data/itos_wt103.pkl'

# Create embedding matrix

# Load training and test set
#train_df = pd.read_csv(TRAIN_CSV, quoting=3, error_bad_lines=False)
train_df = pd.read_csv(TRAIN_CSV)

stops = set(stopwords.words('english'))

def text_to_word_list(text):
    ''' Pre process and convert texts to a list of words '''
    text = str(text)
    text = text.lower()

    # Clean the text
    text = re.sub(r"[^A-Za-z0-9^,!.\/'+-=]", " ", text)
    text = re.sub(r"what's", "what is ", text)
    text = re.sub(r"\'s", " ", text)
    text = re.sub(r"\'ve", " have ", text)
    text = re.sub(r"can't", "cannot ", text)
    text = re.sub(r"n't", " not ", text)
    text = re.sub(r"i'm", "i am ", text)
    text = re.sub(r"\'re", " are ", text)
    text = re.sub(r"\'d", " would ", text)
    text = re.sub(r"\'ll", " will ", text)
    text = re.sub(r",", " ", text)
    text = re.sub(r"\.", " ", text)
    text = re.sub(r"!", " ! ", text)
    text = re.sub(r"\/", " ", text)
    text = re.sub(r"\^", " ^ ", text)
    text = re.sub(r"\+", " + ", text)
    text = re.sub(r"\-", " - ", text)
    text = re.sub(r"\=", " = ", text)
    text = re.sub(r"'", " ", text)
    text = re.sub(r"(\d+)(k)", r"\g<1>000", text)
    text = re.sub(r":", " : ", text)
    text = re.sub(r" e g ", " eg ", text)
    text = re.sub(r" b g ", " bg ", text)
    text = re.sub(r" u s ", " american ", text)
    text = re.sub(r"\0s", "0", text)
    text = re.sub(r" 9 11 ", "911", text)
    text = re.sub(r"e - mail", "email", text)
    text = re.sub(r"j k", "jk", text)
    text = re.sub(r"\s{2,}", " ", text)

    text = text.split()

    return text

# Prepare embedding
vocabulary = dict()
#vocabulary = pickle.load((PR_PATH).open('rb'))
inverse_vocabulary = ['<unk>']
wgts = torch.load(EMBEDDING_FILE, map_location='cpu')

questions_cols = ['orTitle', 'dqTitle']

# Iterate over the questions of training datasets
print("Word encoding...")
for index, row in train_df.iterrows():

    # Iterate through the text of both questions of the row
    for question in questions_cols:

        q2n = []  # q2n -> question numbers representation
        for word in text_to_word_list(row[question]):

            # Check for unwanted words
            # if word in stops and word not in word2vec.vocab:
            #     continue

            if word in stops:
                continue

            if word not in vocabulary:
                vocabulary[word] = len(inverse_vocabulary)
                q2n.append(len(inverse_vocabulary))
                inverse_vocabulary.append(word)
            else:
                q2n.append(vocabulary[word])

        # Replace questions as word to question as number representation
        train_df.set_value(index, question, q2n)

embedding_dim = 400
embeddings = 1 * np.random.randn(len(vocabulary) + 1, embedding_dim)  # This will be the embedding matrix
embeddings[0] = 0  # So that the padding will be ignored

def to_np(v):
    '''returns an np.array object given an input of np.array, list, tuple, torch variable or tensor.'''
    if isinstance(v, (np.ndarray, np.generic)): return v
    if isinstance(v, (list,tuple)): return [to_np(o) for o in v]
    if isinstance(v, Variable): v=v.data
    if torch.cuda.is_available():
        if is_half_tensor(v): v=v.float()
    if isinstance(v, torch.FloatTensor): v=v.float()
    return v.cpu().numpy()


enc_wgts = to_np(wgts['0.encoder.weight'])
row_m = enc_wgts.mean(0)
stoi = collections.defaultdict(lambda:-1, {v:k for k,v in enumerate(vocabulary)})

print("Building embedding matrix...")
for index,word in enumerate(vocabulary):
	r = stoi[word]
	embeddings[index] =enc_wgts[r] if r>=0 else row_m
	
#wgts['0.encoder.weight'] = T(embeddings)
#wgts['0.encoder_with_dropout.embed.weight'] = T(np.copy(embeddings))
#wgts['1.decoder.weight'] = T(np.copy(embeddings))

del wgts


# Prepare training and validation data
print('Preparing training and validation data...')
max_seq_length = max(train_df.orTitle.map(lambda x: len(x)).max(),
                     train_df.dqTitle.map(lambda x: len(x)).max())

print("max_seq_length: " + str(max_seq_length))

# save property for test
#print("Saving property...")
#p = Property(vocabulary, inverse_vocabulary, max_seq_length)
#save_property(p, PROPERTY_PATH)

# Split to train validation
validation_size = 1000
training_size = len(train_df) - validation_size

X = train_df[questions_cols]
Y = train_df['is_duplicate']

X_train, X_validation, Y_train, Y_validation = train_test_split(X, Y, test_size=validation_size)

# Split to dicts
X_train = {'left': X_train.orTitle, 'right': X_train.dqTitle}
X_validation = {'left': X_validation.orTitle, 'right': X_validation.dqTitle}

# Convert labels to their numpy representations
Y_train = Y_train.values
Y_validation = Y_validation.values

# Zero padding
for dataset, side in itertools.product([X_train, X_validation], ['left', 'right']):
    dataset[side] = pad_sequences(dataset[side], maxlen=max_seq_length)


# Make sure everything is ok
assert X_train['left'].shape == X_train['right'].shape
assert len(X_train['left']) == len(Y_train)

# Build the model

# Model variables
n_hidden = 50
#gradient_clipping_norm = 1.25
batch_size = 64
n_epoch = 25

# The visible layer
left_input = Input(shape=(max_seq_length,), dtype='int32')
right_input = Input(shape=(max_seq_length,), dtype='int32')

# Since this is a siamese network, both sides share the same LSTM
def shared_lstm(_input):
	# Embedded version of the inputs
	embedded = Embedding(len(embeddings), embedding_dim, weights=[embeddings], input_shape=(max_seq_length,),
                         trainable=False)(_input)
	# Multilayer Bi-LSTM
	activations = Bidirectional(LSTM(n_hidden, return_sequences=True), merge_mode='concat')(embedded)
	activations = Bidirectional(LSTM(n_hidden, return_sequences=True), merge_mode='concat')(activations)

	# dropout
	# activations = Dropout(0.5)(activations)

	# Attention Mechanism
	attention = TimeDistributed(Dense(1, activation='tanh'))(activations)
	attention = Flatten()(attention)
	attention = Activation('softmax')(attention)
	attention = RepeatVector(n_hidden * 2)(attention)
	attention = Permute([2, 1])(attention)
	sent_representation = multiply([activations, attention])
	sent_representation = Lambda(lambda xin: K.sum(xin, axis=1))(sent_representation)

	# dropout
	# sent_representation = Dropout(0.1)(sent_representation)

	return sent_representation

left_sent_representation = shared_lstm(left_input)
right_sent_representation = shared_lstm(right_input)

malstm_distance = ManDist()([left_sent_representation, right_sent_representation])
sen_representation = concatenate([left_sent_representation, right_sent_representation, malstm_distance])
similarity = Dense(1, activation='sigmoid')(Dense(2)(Dense(4)(Dense(16)(sen_representation))))

# Pack it all up into a model
malstm = Model(inputs=[left_input, right_input], outputs=[similarity])
# Adadelta optimizer, with gradient clipping by norm
#optimizer = Adadelta(clipnorm=gradient_clipping_norm)
optimizer=keras.optimizers.Adam()

malstm.compile(loss='mean_squared_error', optimizer=optimizer, metrics=['accuracy'])
    
# record weights bias
model_checkpoint = ModelCheckpoint('weights.h5', monitor='val_loss', save_best_only=True)
    
# csv logger
csv_logger = CSVLogger('log10.csv', append=True, separator=',')

# Start training
print('Training...')
training_start_time = time()

malstm_trained = malstm.fit([X_train['left'], X_train['right']], Y_train, batch_size=batch_size, nb_epoch=n_epoch,
                            validation_data=([X_validation['left'], X_validation['right']], Y_validation),
							callbacks=[model_checkpoint, csv_logger])

print("Training time finished.\n{} epochs in {}".format(n_epoch, datetime.timedelta(seconds=time()-training_start_time)))


