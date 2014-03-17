# history.py
# Interfaces to the history of transactions.
#
# Copyright (C) 2013  Red Hat, Inc.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# the GNU General Public License v.2, or (at your option) any later version.
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY expressed or implied, including the implied warranties of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.  You should have received a copy of the
# GNU General Public License along with this program; if not, write to the
# Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.  Any Red Hat trademarks that are incorporated in the
# source code or documentation are not subject to the GNU General Public
# License and may only be used or replicated with the express permission of
# Red Hat, Inc.
#

"""Interfaces to the history of transactions."""

from __future__ import absolute_import
from collections import defaultdict, Container, Iterable, Sized
from dnf.util import is_exhausted, split_by
from dnf.subject import Subject
import swdb
import glob

INSTALLING_STATES = {'Install', 'Reinstall', 'Update', 'Downgrade'}

PRIMARY_STATES = {'Install', 'Erase', 'Reinstall', 'Downgrade', 'Update'}

REMOVING_STATES = {'Erase', 'Reinstalled', 'Updated', 'Downgraded', 'Obsoleted'}

STATE2OPPOSITE = {'Install': 'Erase',
                  'Erase': 'Install',
                  'Reinstall': 'Reinstall',
                  'Update': 'Downgrade',
                  'Downgrade': 'Update'}

STATE2COMPLEMENT = {'Reinstall': 'Reinstalled',
                    'Reinstalled': 'Reinstall',
                    'Update': 'Updated',
                    'Updated': 'Update',
                    'Downgrade': 'Downgraded',
                    'Downgraded': 'Downgrade'}

_valid_rpmdb_keys = set(["buildtime", "buildhost",
                         "license", "packager",
                         "size", "sourcerpm", "url", "vendor",
                         "committer", "committime"])

class History(swdb.Swdb):
    """Transactions history interface on top of an YumHistory."""

    def __init__(self, using_pkgs):
        """Initialize a wrapper instance."""
        object.__init__(self)
        pkgs = map(self.swdb_pkg, using_pkgs)
        super(swdb.Swdb, self).__init__(default_type=swdb.RPM_PKGS, app=pkgs)

    def __enter__(self):
        """Enter the runtime context."""
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        """Exit the runtime context."""
        self.close()
        return False

    def begin_transaction(self, transaction, cmdline, rpmdb_version):
        self.transaction.new(
            pkgs=self.trans2swdbpkg(transaction),
            cmdline=cmdline,
            rpmdb_version=rpmdb_version)

    def save_rpmdb_data(self, pkg):
        """ Save all the data for rpmdb for this installed pkg, assumes
            there is no data currently. """
        attrs = {key: getattr(pkg, key, None) for key in _valid_rpmdb_keys}
        not_empty_attrs = filter(lambda a: attrs[a], attrs)
        dwdbpkg = self.swdb_pkg(pkg)
        return dwdbpkg.set(**not_empty_attrs).save()

    def pkg_stats(self):
        distinct_fields = (
            ('na', ('name', 'arch')),
            ('nevrac', ('name', 'arch')),
            ('nevra', ('name', 'arch')),
            ('nevr', ('name', 'arch')),
            ('rpmdb', ('name', 'arch')),
            ('yumdb', ('name', 'arch'))
            # TODO assign right fields
        )

        def records_count(*fields):
            return len(self.base.history.package.all().distinct(*fields))

        return {key: records_count(fields) for key, fields in distinct_fields}

    def search(self, patterns):
        """ Search for history transactions which contain specified
            packages al. la. "yum list". Returns transaction ids. """
        tids = []
        for nevra in Subject(patterns).nevra_possibilities():
            attrs = ((a, val) for a, val in nevra.__dict__.iteritems() if a)
            filters = {"pkgs__" + attr + "__icase": val for (attr, val) in attrs}
            tids.append(lambda t: t.tid, map(self.swdb.pkgs.filter(**filters)))
        return tids

    def swdb_pkg(self, hypkg):
        attrs = ("name", "epoch", "version", "release", "arch")
        return self.package.new(**{a: getattr(hypkg, a) for a in attrs})

    def trans2swdbpkg(self, transaction):
        # TODO add packages as pairs
        for tsi in transaction:
            for (pkg, state) in tsi.history_iterator():
                yield self.swdb_pkg(pkg).set({"state": state})

    def return_addon_data(self, tid, item=None):
        # temporary maintained
        hist_and_tid = self.conf.addon_path + '/' + str(tid) + '/'
        addon_info = glob.glob(hist_and_tid + '*')
        addon_names = [i.replace(hist_and_tid, '') for i in addon_info]
        if not item:
            return addon_names

        if item not in addon_names:
            # XXX history needs SOME kind of exception, or warning, I think?
            return None

        fo = open(hist_and_tid + item, 'r')
        data = fo.read()
        fo.close()
        return data

    def write_addon_data(self, dataname, data):
        # temporary maintained
        """append data to an arbitrary-named file in the history
           addon_path/transaction id location,
           returns True if write succeeded, False if not"""

        if not hasattr(self, '_tid'):
            # maybe we should raise an exception or a warning here?
            return False

        if not dataname:
            return False

        if not data:
            return False

        # make sure the tid dir exists
        tid_dir = self.conf.addon_path + '/' + str(self._tid)

        if self.conf.writable and not os.path.exists(tid_dir):
            try:
                os.makedirs(tid_dir, mode=0o700)
            except (IOError, OSError) as e:
                # emit a warning/raise an exception?
                return False

        # cleanup dataname
        safename = dataname.replace('/', '_')
        data_fn = tid_dir + '/' + safename
        try:
            # open file in append
            fo = open(data_fn, 'wb+')
            # write data
            fo.write(to_utf8(data))
            # flush data
            fo.flush()
            fo.close()
        except (IOError, OSError) as e:
            return False
        # return
        return True

    def transaction_nevra_ops(self, id_):
        """Get operations on packages (by their NEVRAs) in the transaction."""
        try:
            hpkgs = self._swdb.history_transactions.filter(tid=id_)[0]
        except IndexError:
            raise ValueError('no transaction with given ID: %d' % id_)

        # Split history to history packages representing transaction items.
        items_hpkgs = split_by(hpkgs, lambda hpkg: hpkg.state in PRIMARY_STATES)

        # First item should be empty if the first state is valid.
        empty_item_hpkgs = next(items_hpkgs)
        assert not empty_item_hpkgs  # is empty

        # Return the operations.
        operations = NEVRAOperations()
        for item_hpkgs in items_hpkgs:
            obsoleted_nevras = []
            obsoleting_nevra = None
            replaced_nevra, replaced_state = None, None

            # It is easier to traverse the packages in the reversed order.
            reversed_it = reversed(tuple(item_hpkgs))
            hpkg = next(reversed_it)

            while hpkg.state == 'Obsoleted':  # Read obsoleted packages.
                obsoleted_nevras.append(hpkg.nevra)
                hpkg = next(reversed_it)
            if obsoleted_nevras:  # Read obsoleting package.
                assert hpkg.state == 'Obsoleting'
                obsoleting_nevra = hpkg.nevra
                hpkg = next(reversed_it)
            if hpkg.state in {'Reinstalled', 'Downgraded', 'Updated'}:  # Replaced.
                replaced_nevra, replaced_state = hpkg.nevra, hpkg.state
                hpkg = next(reversed_it)
            assert is_exhausted(reversed_it)
            assert not obsoleting_nevra or obsoleting_nevra == hpkg.nevra
            assert not replaced_state or replaced_state == STATE2COMPLEMENT[hpkg.state]

            operations.add(hpkg.state, hpkg.nevra, replaced_nevra, obsoleted_nevras)
        return operations

class NEVRAOperations(Sized, Iterable, Container):
    """Mutable container of operations on packages by their NEVRAs."""

    def __init__(self):
        """Initialize a wrapper instance."""
        self._nevra2primary_state = {}
        self._replaced_by = {}
        self._obsoleted_by = defaultdict(set)

    def __add__(self, other):
        """Compute the sum of *self* and the *other* one."""
        result = NEVRAOperations()
        for state, nevra, replaced_nevra, obsoleted_nevras in self:
            result.add(state, nevra, replaced_nevra, obsoleted_nevras)
        for state, nevra, replaced_nevra, obsoleted_nevras in other:
            result.add(state, nevra, replaced_nevra, obsoleted_nevras)
        return result

    def __contains__(self, operation):
        """Test whether the *operation* is in *self*."""
        try:
            state, nevra, replaced, obsoleted = operation
        except ValueError:
            return False
        try:
            state_ = self._nevra2primary_state[nevra]
        except KeyError:
            return False
        if state_ != state:
            return False
        replaced_ = self._replaced_by.get(nevra, None)
        if replaced_ != replaced:
            return False
        obsoleted_ = self._obsoleted_by[nevra]
        return set(obsoleted_) == set(obsoleted)

    def __eq__(self, other):
        """Test whether *self* is equal to the *other* one."""
        if self is other:
            return True
        if type(self) is not type(other):
            return False
        if len(self) != len(other):
            return False
        return all(operation in other for operation in self)

    def __iter__(self):
        """Get iterator over the contained operations."""
        return (
            (state, nevra, self._replaced_by.get(nevra, None), self._obsoleted_by[nevra])
            for nevra, state in self._nevra2primary_state.items())

    def __len__(self):
        """Compute the number of contained operations."""
        return len(self._nevra2primary_state.items())

    def __ne__(self, other):
        """Test whether *self* is not equal to the *other* one."""
        return not self == other

    def _add_erase(self, old_nevra):
        """Add new erase of the *old_nevra*."""
        state = self._state(old_nevra, None)
        if state is None:
            self._set_primary_state(old_nevra, 'Erase')
        else:
            if state in REMOVING_STATES:
                raise ValueError('NEVRA was already removed: %s' % old_nevra)
            elif state in INSTALLING_STATES:
                self._unset_primary_state(old_nevra)
            else:
                assert False

    def _add_install(self, new_nevra):
        """Add new install of the *new_nevra*."""
        state = self._state(new_nevra, None)
        if state is None:
            self._set_primary_state(new_nevra, 'Install')
        else:
            if state in INSTALLING_STATES:
                raise ValueError('NEVRA was already installed: %s' % new_nevra)
            elif state in REMOVING_STATES:
                self._set_primary_state(new_nevra, 'Reinstall', new_nevra)
            else:
                assert False

    def _add_obsoleted(self, obsoleting_nevra, obsoleted_nevra):
        """Add new *obsoleted_nevra* obsoleted by the *obsoleting_nevra*."""
        state = self._state(obsoleted_nevra, None)
        if state in {None, 'Obsoleted'}:
            self._set_obsoleted_state(obsoleting_nevra, obsoleted_nevra)
        elif state in REMOVING_STATES:
            assert state != 'Obsoleted'
            raise ValueError('NEVRA was already removed: %s' % obsoleted_nevra)
        elif state in INSTALLING_STATES:
            self._unset_primary_state(obsoleted_nevra)

    def _add_replacement(self, state, new_nevra, old_nevra):
        """Add new *new_nevra* replacing the *old_nevra* using the *state*."""
        assert state in {'Reinstall', 'Update', 'Downgrade'}

        old_state, new_state = self._state(old_nevra, None), self._state(new_nevra, None)
        if old_state is None and new_state is None:
            self._set_primary_state(new_nevra, state, old_nevra)
        elif old_state in REMOVING_STATES:
            raise ValueError('NEVRA was already removed: %s' % old_nevra)
        elif new_state in INSTALLING_STATES:
            if old_nevra != new_nevra:
                raise ValueError('NEVRA was already installed: %s' % new_nevra)
            # Following applies only for reinstallation of the NEVRA by the
            # same NEVRA. Do nothing.
            assert state == 'Reinstall'
        elif old_state == 'Reinstall' and new_state is None:
            # If a reinstall precedes, replace it by the new replacement but
            # use the replaced NEVRA of the reinstall as the new replaced NEVRA.
            self._combine_replacements(old_nevra, new_nevra, state)
        elif old_state == STATE2OPPOSITE[state] and new_state is None:
            # Following does not apply for for reinstalls. If the opposite
            # state precedes, replace it by an erase&install because it is
            # not clear, whether the result is an update or a downgrade.
            assert state != 'Reinstall'
            self._combine_replacements(old_nevra, new_nevra, 'Install')
        elif old_state == STATE2OPPOSITE[state] and self._replaced_by[old_nevra] == new_nevra:
            # If the opposite state with same both NEVRAs (but swapped)
            # precedes, replace it by a reinstall of the new replacing NEVRA
            # and an erase of the new replaced NEVRA.
            assert new_state == (STATE2COMPLEMENT[old_state]
                                 if old_nevra != new_nevra else 'Reinstall')
            self._combine_replacements(old_nevra, new_nevra, 'Reinstall')
        elif old_state == 'Install' and new_state == 'Erase':
            # If a manual replacement (erase&install) precedes, reinstall the
            # new replacing NEVRA and forget the new replacement.
            self._unset_primary_state(old_nevra)
            self._set_primary_state(new_nevra, 'Reinstall', new_nevra)
        elif old_state in INSTALLING_STATES and new_state is None:  # Remaining old states.
            # If the old_state does not match any previous conditions and if
            # it is a primary state, replace the old replacing NEVRA by the
            # new one.
            self._combine_replacements(old_nevra, new_nevra, old_state)
        elif old_state is None and new_state in REMOVING_STATES:
            # If the new replacing NEVRA was removed, reinstall it, install
            # the old replacing NEVRA and remove the new replaced NEVRA.
            try:
                replacement_nevra = self._replaces(new_nevra)
            except ValueError:
                pass
            else:
                self._set_primary_state(replacement_nevra, 'Install')
            self._set_primary_state(old_nevra, 'Erase')
            self._set_primary_state(new_nevra, 'Reinstall', new_nevra)
        else:
            assert False

    def _combine_replacements(self, old_nevra, new_nevra, state):
        """Combine the *old_nevra* operation with the *new_nevra* into the *state*."""
        replaced_nevra = None if state == 'Install' else self._replaced_by[old_nevra]
        self._unset_primary_state(old_nevra)
        self._set_primary_state(new_nevra, state, replaced_nevra)

    def _replaces(self, old_nevra, default=False):
        """Get the NEVRA replacing the *old_nevra*."""
        replacements = iter(self._replaced_by.items())
        for key, value in replacements:
            if value == old_nevra:
                assert all(val != old_nevra for val, _key in replacements)
                return key

        if default is not False:
            return default
        raise ValueError('no replacement for NEVRA: %s' % old_nevra)

    def _set_obsoleted_state(self, obsoleting_nevra, obsoleted_nevra):
        """Set the *obsoleting_nevra* as an obsoleting and the *obsoleted_nevra* as an obsoleted."""
        self._nevra2primary_state.pop(obsoleted_nevra, None)
        for replacement_nevra, replaced_nevra in self._replaced_by.items():
            if replaced_nevra == obsoleted_nevra:
                del self._replaced_by[replacement_nevra]
        self._obsoleted_by[obsoleting_nevra].add(obsoleted_nevra)

    def _set_primary_state(self, nevra, state, replaced_nevra=None):
        """Set the *nevra* in the *state* and the *replaced_nevra* as replaced."""
        self._nevra2primary_state[nevra] = state
        for replacement_nevra, replaced_nevra_ in list(self._replaced_by.items()):
            if replaced_nevra_ == nevra:
                del self._replaced_by[replacement_nevra]
        for obsoleted_nevras in self._obsoleted_by.values():
            obsoleted_nevras.discard(nevra)

        if replaced_nevra is not None:
            if replaced_nevra != nevra:
                self._nevra2primary_state.pop(replaced_nevra, None)
            for replacement_nevra, replaced_nevra_ in self._replaced_by.items():
                if replaced_nevra_ == replaced_nevra:
                    del self._replaced_by[replacement_nevra]
            self._replaced_by[nevra] = replaced_nevra
            for obsoleted_nevras in self._obsoleted_by.values():
                obsoleted_nevras.discard(replaced_nevra)

    def _state(self, nevra, default=False):
        """Get the state of the *nevra*."""
        try:
            state = self._nevra2primary_state[nevra]
        except KeyError:
            pass
        else:
            assert all(nevra not in obsoleted for obsoleted in self._obsoleted_by.values())
            assert ((state == 'Reinstall' and self._replaced_by[nevra] == nevra) or
                    self._replaces(nevra, None) is None)
            return state

        try:
            replacement_nevra = self._replaces(nevra)
        except ValueError:
            pass
        else:
            assert all(nevra not in obsoleted for obsoleted in self._obsoleted_by.values())
            replacement_state = self._state(replacement_nevra)
            return STATE2COMPLEMENT[replacement_state]

        if any(nevra in obsoleted for obsoleted in self._obsoleted_by.values()):
            return 'Obsoleted'

        if default is not False:
            return default

        raise ValueError('no state of NEVRA: %s' % nevra)

    def _unset_primary_state(self, nevra):
        """Unset primary state of the *nevra*."""
        state = self._nevra2primary_state.pop(nevra)
        assert ((state == 'Reinstall' and self._replaced_by[nevra] == nevra) or
                self._replaces(nevra, None) is None)
        assert all(nevra not in obsoleted for obsoleted in self._obsoleted_by.values())
        try:
            replaced_nevra = self._replaced_by[nevra]
        except KeyError:
            assert state == 'Install'
        else:
            self._set_primary_state(replaced_nevra, 'Erase')
        for obsoleted_nevra in self._obsoleted_by.pop(nevra, ()):
            self._set_primary_state(obsoleted_nevra, 'Erase')

    def add(self, state, nevra, replaced_nevra=None, obsoleted_nevras=()):
        """Add new *nevra* in the *state* replacing and obsoleting other NEVRAs."""
        if state == 'Install':
            if replaced_nevra:
                raise ValueError('Install cannot replace anything: %s'
                                 % replaced_nevra)
            self._add_install(nevra)
        elif state == 'Erase':
            if replaced_nevra or obsoleted_nevras:
                raise ValueError('Erase cannot replace/obsolete anything: %s' %
                                 replaced_nevra or obsoleted_nevras)
            self._add_erase(nevra)
        elif state in {'Reinstall', 'Downgrade', 'Update'}:
            self._add_replacement(state, nevra, replaced_nevra)
        else:
            raise ValueError('unknown operation: %s' % state)

        for obsoleted_nevra in obsoleted_nevras:
            self._add_obsoleted(nevra, obsoleted_nevra)
