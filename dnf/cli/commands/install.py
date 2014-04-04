# install.py
# Install CLI command.
#
# Copyright (C) 2014 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import unicode_literals
from .. import commands
from dnf.i18n import _

import dnf.exceptions
import functools
import operator

class InstallCommand(commands.Command):
    """A class containing methods needed by the cli to execute the
    install command.
    """

    aliases = ('install',)
    activate_sack = True
    resolve = True
    writes_rpmdb = True

    @staticmethod
    def get_usage():
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return _("PACKAGE...")

    @staticmethod
    def get_summary():
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Install a package or packages on your system")

    def doCheck(self, basecmd, extcmds):
        """Verify that conditions are met so that this command can run.
        These include that the program is being run by the root user,
        that there are enabled repositories with gpg keys, and that
        this command is called with appropriate arguments.

        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        commands.checkGPGKey(self.base, self.cli)
        commands.checkPackageArg(self.cli, basecmd, extcmds)
        commands.checkEnabledRepo(self.base, extcmds)

    @staticmethod
    def parse_extcmds(extcmds):
        """Parse command arguments."""
        pkg_specs, grp_specs, filenames = [], [], []
        for argument in extcmds:
            if argument.endswith('.rpm'):
                filenames.append(argument)
            elif argument.startswith('@'):
                grp_specs.append(argument[1:])
            else:
                pkg_specs.append(argument)
        return pkg_specs, grp_specs, filenames

    def run(self, extcmds):
        pkg_specs, grp_specs, filenames = self.parse_extcmds(extcmds)

        # Install files.
        local_pkgs = map(self.base.add_remote_rpm, filenames)
        results = map(self.base.package_install, local_pkgs)
        done = functools.reduce(operator.or_, results, False)

        # Install groups.
        if grp_specs:
            self.base.read_comps()
        cnt = 0
        for spec in grp_specs:
            group = self.base.comps.group_by_pattern(spec)
            if group is None:
                msg = _("Warning: Group '%s' does not exist.")
                self.base.logger.error(msg, dnf.i18n.ucd(spec))
                continue
            cnt += self.base.group_install(group, dnf.const.GROUP_PACKAGE_TYPES)
        if grp_specs and not cnt:
            msg = _('No packages in any requested group available '\
                    'to install or upgrade.')
            raise dnf.exceptions.Error(msg)
        elif cnt:
            done = True

        # Install packages.
        for pkg_spec in pkg_specs:
            try:
                self.base.install(pkg_spec)
            except dnf.exceptions.MarkingError:
                msg = _('No package %s%s%s available.')
                self.base.logger.info(
                    msg, self.base.output.term.MODE['bold'], pkg_spec,
                    self.base.output.term.MODE['normal'])
            else:
                done = True

        if not done:
            raise dnf.exceptions.Error(_('Nothing to do.'))
