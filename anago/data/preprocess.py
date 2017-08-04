import collections
import itertools
import os
import re

import numpy as np
from keras.preprocessing import sequence
from keras.utils.np_utils import to_categorical
from sklearn.base import BaseEstimator, TransformerMixin

from anago.data.reader import UNK, PAD
from anago.data_utils import write_vocab, load_glove_vocab, UNK, NUM
Vocab = collections.namedtuple('Vocab', ['word', 'char', 'tag'])


def pad_words(xs, config):
    return sequence.pad_sequences(xs, maxlen=config.num_steps, padding='post')


def to_onehot(ys, config, ntags):
    ys = sequence.pad_sequences(ys, maxlen=config.num_steps, padding='post')
    return np.asarray([to_categorical(y, num_classes=ntags) for y in ys])


def get_processing_word(vocab_words=None, vocab_chars=None, lowercase=False, use_char=False):
    """
    Args:
        vocab_words: dict[word] = idx
        vocab_chars: dict[char] = idx
        lowercase: if True, word is converted to lowercase
    Returns:
        f("cat") = ([12, 4, 32], 12345)
                 = (list of char ids, word id)
    """
    def f(word):
        # 0. preprocess word
        if lowercase:
            word = word.lower()
        word = digit_to_zero(word)

        # 1. get chars of words
        if vocab_chars is not None and use_char:
            char_ids = []
            for char in word:
                # ignore chars out of vocabulary
                if char in vocab_chars:
                    char_ids += [vocab_chars.get(char, vocab_chars[UNK])]

        # 2. get id of word
        if vocab_words is not None:
            word = vocab_words.get(word, vocab_words.get(UNK, vocab_words[PAD]))

        # 3. return tuple char ids, word id
        if vocab_chars is not None and use_char:
            return char_ids, word
        else:
            return word

    return f


def digit_to_zero(word):
    return re.sub(r'[0-9０１２３４５６７８９]', r'0', word)


def pad_word_chars(words, max_word_len):
    words_for = []
    for word in words:
        padding = [0] * (max_word_len - len(word))
        padded_word = word + padding
        words_for.append(padded_word[:max_word_len])
    return words_for


def pad_words1(words, max_word_len, max_sent_len):
    padding = [[0] * max_word_len for i in range(max_sent_len - len(words))]
    words += padding
    return words[:max_sent_len]


def pad_chars(dataset, config):
    result = []
    for sent in dataset:
        words = pad_word_chars(sent, config.max_word_len)
        words = pad_words1(words, config.max_word_len, config.num_steps)
        result.append(words)
    return np.asarray(result)


def get_vocabs(datasets):
    """
    Args:
        datasets: a list of dataset objects
    Return:
        a set of all the words in the dataset
    """
    print("Building vocab...")
    vocab_words = set()
    vocab_tags = set()
    for dataset in datasets:
        for words, tags in zip(dataset.sents, dataset.labels):
            vocab_words.update(words)
            vocab_tags.update(tags)
    print("- done. {} tokens".format(len(vocab_words)))
    return vocab_words, vocab_tags


def get_char_vocab(dataset):
    """
    Args:
        dataset: a iterator yielding tuples (sentence, tags)
    Returns:
        a set of all the characters in the dataset
    """
    vocab_char = set()
    for words in dataset.sents:
        for word in words:
            vocab_char.update(word)

    return vocab_char



def get_vocab_path(base_path):
    word_file = os.path.join(base_path, 'words.txt')
    char_file = os.path.join(base_path, 'chars.txt')
    tag_file = os.path.join(base_path, 'tags.txt')
    return word_file, char_file, tag_file


def build_vocab(dataset, config):
    processing_word = get_processing_word(lowercase=True)

    # Build Word and Tag vocab
    vocab_words, vocab_tags = get_vocabs([dataset.train, dataset.valid, dataset.test])
    vocab_glove = load_glove_vocab(config.glove_path)
    vocab_chars = get_char_vocab(dataset.train)

    vocab_words = vocab_words & vocab_glove
    vocab_words.add(UNK)
    vocab_words.add(NUM)

    # Save vocab
    word_file, char_file, tag_file = get_vocab_path(config.save_path)
    write_vocab(vocab_words, word_file)
    write_vocab(vocab_chars, char_file)
    write_vocab(vocab_tags, tag_file)

    vocab = load_vocab(config.save_path)

    return vocab


def load_vocab(save_path):

    def func(filename):
        print('Loading vocab...')
        with open(filename) as f:
            v = {w.rstrip(): i for i, w in enumerate(f)}
        print('- done. {} tokens'.format(len(v)))
        return v

    word_file, char_file, tag_file = get_vocab_path(save_path)
    words = func(word_file)
    chars = func(char_file)
    tags  = func(tag_file)

    return Vocab(word=words, char=chars, tag=tags)


def load_word_embeddings(vocab, glove_filename, dim):
    """Loads GloVe vectors in numpy array

    Arguments:
        vocab: dictionary vocab[word] = index
        glove_filename: a path to a glove file
        dim: (int) dimension of embeddings
    """
    embeddings = np.zeros([len(vocab), dim])
    with open(glove_filename) as f:
        for line in f:
            line = line.strip().split(' ')
            word = line[0]
            embedding = [float(x) for x in line[1:]]
            if word in vocab:
                word_idx = vocab[word]
                embeddings[word_idx] = np.asarray(embedding)

    return embeddings


class WordPreprocessor(BaseEstimator, TransformerMixin):

    def __init__(self, lowercase=True, num_norm=True, char_feature=True, vocab_init=None):
        self.lowercase = lowercase
        self.num_norm = num_norm
        self.char_feature = char_feature
        self.vocab_word = None
        self.vocab_char = None
        self.vocab_tag  = None
        self.vocab_init = vocab_init or {}

    def fit(self, X, y):
        words = {UNK: 0}
        chars = {UNK: 0}
        tags  = {}

        for w in set(itertools.chain(*X)) | set(self.vocab_init):
            w = self._lower(w)
            w = self._normalize_num(w)
            if w not in words:
                words[w] = len(words)

            if not self.char_feature:
                continue
            for c in w:
                if c not in chars:
                    chars[c] = len(chars)

        for t in itertools.chain(*y):
            if t not in tags:
                tags[t] = len(tags)

        self.vocab_word = words
        self.vocab_char = chars
        self.vocab_tag  = tags

        return self

    def transform(self, X, y):
        sents = []
        for sent in X:
            word_ids = []
            char_word_ids = []
            for w in sent:
                w = self._lower(w)
                w = self._normalize_num(w)
                if w in self.vocab_word:
                    word_id = self.vocab_word[w]
                else:
                    word_id = self.vocab_word[UNK]
                word_ids.append(word_id)

                if self.char_feature:
                    char_ids = self._get_char_ids(w)
                    char_word_ids.append(char_ids)

            if self.char_feature:
                sents.append((char_word_ids, word_ids))
            else:
                sents.append(word_ids)

        y = [[self.vocab_tag[t] for t in sent] for sent in y]

        return sents, y

    def inverse_transform(self, y):
        indice_tag = {i: t for t, i in self.vocab_tag.items()}
        return [indice_tag[y_] for y_ in y]

    def _get_char_ids(self, word):
        return [self.vocab_char.get(c, self.vocab_char[UNK]) for c in word]

    def _lower(self, word):
        return word.lower() if self.lowercase else word

    def _normalize_num(self, word):
        if self.num_norm:
            return re.sub(r'[0-9０１２３４５６７８９]', r'0', word)
        else:
            return word
