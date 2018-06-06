"""
Copyright 2018 The Matrix Authors
This file is part of the Matrix library.

The Matrix library is free software: you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

The Matrix library is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License
along with the Matrix library. If not, see <http://www.gnu.org/licenses/>.
"""
#!/usr/bin/env python
# -*- coding: utf-8 -*-
# File: collection.py


import tensorflow as tf
from copy import copy
import six
from contextlib import contextmanager

from ..utils import logger
from ..utils.argtools import memoized

__all__ = ['backup_collection',
           'restore_collection',
           'freeze_collection']


def backup_collection(keys=None):
    """
    Args:
        keys (list): list of collection keys to backup.
            Defaults to all keys in the graph.

    Returns:
        dict: the backup
    """
    if keys is None:
        keys = tf.get_default_graph().get_all_collection_keys()
    ret = {}
    assert isinstance(keys, (list, tuple, set))
    for k in keys:
        ret[k] = copy(tf.get_collection(k))
    return ret


def restore_collection(backup):
    """
    Restore from a collection backup.

    Args:
        backup (dict):
    """
    for k, v in six.iteritems(backup):
        del tf.get_collection_ref(k)[:]
        tf.get_collection_ref(k).extend(v)


@contextmanager
def freeze_collection(keys):
    """
    Args:
        keys(list): list of collection keys to freeze.

    Returns:
        a context where the collections are in the end restored to its initial state.
    """
    backup = backup_collection(keys)
    yield
    restore_collection(backup)


@memoized
def get_inverse_graphkeys():
    ret = {}
    for name in dir(tf.GraphKeys):
        if name.startswith('_'):
            continue
        if name in ['VARIABLES']:   # will produce deprecated warning
            continue
        ret[getattr(tf.GraphKeys, name)] = "tf.GraphKeys.{}".format(name)
    return ret


class CollectionGuard(object):
    """
    A context to maintain collection change in a tower.
    """

    original = None

    def __init__(self, name, check_diff,
                 freeze_keys=[],
                 diff_whitelist=None):
        """
        Args:
           name (str): name of the tower
           check_diff (bool): whether to check and print about collection change
                when leaving this guard.
           freeze_keys (list): list of keys to freeze
           diff_whitelist (list): list of keys to ignore, when check_diff is True.
                Defaults to some collections that are normally changed,
                including variables, losses, contexts, queue runners.
        """
        self._name = name
        self._check_diff = check_diff
        if diff_whitelist is None:
            diff_whitelist = CollectionGuard._default_diff_whitelist()
        self._whitelist = set(diff_whitelist)
        self._freeze_keys = freeze_keys
        self._inverse_graphkeys = get_inverse_graphkeys()

    @staticmethod
    def _default_diff_whitelist():
        ret = [tf.GraphKeys.TRAINABLE_VARIABLES,
               tf.GraphKeys.GLOBAL_VARIABLES,
               tf.GraphKeys.QUEUE_RUNNERS,
               tf.GraphKeys.LOCAL_VARIABLES]
        for newkey in ['COND_CONTEXT', 'WHILE_CONTEXT', 'LOSSES']:
            if hasattr(tf.GraphKeys, newkey):
                ret.append(getattr(tf.GraphKeys, newkey))
        return ret

    def _key_name(self, name):
        return self._inverse_graphkeys.get(name, name)

    def __enter__(self):
        self.original = backup_collection()
        self._freeze_backup = backup_collection(self._freeze_keys)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            return False
        new_coll = backup_collection()

        if self._check_diff:
            self._print_diff(new_coll)
        self._restore_freeze(new_coll)
        return False

    def _print_diff(self, new):
        newly_created = []
        size_change = []
        for k, v in six.iteritems(new):
            if k in self._whitelist or k in self._freeze_keys:
                continue
            if k not in self.original:
                newly_created.append(self._key_name(k))
            else:
                old_v = self.original[k]
                if len(old_v) != len(v):
                    size_change.append((self._key_name(k), len(old_v), len(v)))
        if newly_created:
            logger.info(
                "New collections created in {}: {}".format(
                    self._name, ', '.join(newly_created)))
        if size_change:
            logger.info(
                "Size of these collections were changed in {}: {}".format(
                    self._name, ', '.join(
                        map(lambda t: "({}: {}->{})".format(*t),
                            size_change))))

    def _restore_freeze(self, new):
        size_change = []
        for k, v in six.iteritems(self._freeze_backup):
            newv = new.get(k, [])
            if len(v) != len(newv):
                size_change.append((self._key_name(k), len(v), len(newv)))
        if size_change:
            logger.info(
                "These collections were modified but restored in {}: {}".format(
                    self._name, ', '.join(
                        map(lambda t: "({}: {}->{})".format(*t),
                            size_change))))
        restore_collection(self._freeze_backup)

    def get_collection_in_tower(self, key):
        """
        Get items from this collection that are added in the current tower.
        """
        new = tf.get_collection(key)
        old = set(self.original.get(key, []))
        # presist the order in new
        return [x for x in new if x not in old]