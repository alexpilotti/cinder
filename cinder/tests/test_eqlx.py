import mox
import paramiko

from cinder import context
from cinder import exception
from cinder import flags
from cinder.openstack.common import log as logging
from cinder import test
from cinder.volume import configuration as conf
from cinder.volume.drivers import eqlx

LOG = logging.getLogger(__name__)
FLAGS = flags.FLAGS


class DellEQLSanISCSIDriverTestCase(test.TestCase):

    def setUp(self):
        super(DellEQLSanISCSIDriverTestCase, self).setUp()
        configuration = mox.MockObject(conf.Configuration)
        configuration.san_is_local = False
        configuration.san_ip = "10.0.0.1"
        configuration.san_login = "foo"
        configuration.san_password = "bar"
        configuration.san_ssh_port = 16022
        configuration.san_thin_provision = True
        configuration.append_config_values(mox.IgnoreArg())
        FLAGS.eqlx_pool = 'non-default'
        FLAGS.eqlx_use_chap = True
        FLAGS.eqlx_verbose_ssh = True
        self._context = context.get_admin_context()
        self.driver = eqlx.DellEQLSanISCSIDriver(configuration=configuration)
        self.driver._execute = self.mox.CreateMock(self.driver._execute)
        self.volume_name = "fakevolume"
        self.connector = {'ip': '10.0.0.2',
                          'initiator': 'iqn.1993-08.org.debian:01:222',
                          'host': 'fakehost'}
        self.fake_iqn = 'iqn.2003-10.com.equallogic:group01:25366:fakev'
        self.driver._group_ip = '10.0.1.6'
        self.properties = {
            'target_discoverd': True,
            'target_portal': '%s:3260' % self.driver._group_ip,
            'target_iqn': self.fake_iqn,
            'volume_id': 1}
        self._model_update = {
            'provider_location': "%s:3260,1 %s 0" % (self.driver._group_ip,
                                                     self.fake_iqn),
            'provider_auth': 'CHAP %s %s' % (FLAGS.eqlx_chap_login,
                                             FLAGS.eqlx_chap_password)
        }

    def _fake_get_iscsi_properties(self, volume):
        return self.properties

    def _fake_execute(self, *args, **kwargs):
        pass

    def test_create_volume(self):
        volume = {'name': self.volume_name, 'size': 1}
        self.driver._execute('volume', 'create', volume['name'],
                             "%sG" % (volume['size']), 'pool',
                             FLAGS.eqlx_pool, 'thin-provision').\
            AndReturn(['iSCSI target name is %s.' % self.fake_iqn])
        self.mox.ReplayAll()
        model_update = self.driver.create_volume(volume)
        self.assertEqual(model_update, self._model_update)

    def test_delete_volume(self):
        volume = {'name': self.volume_name, 'size': 1}
        self.driver._execute('volume', 'select', volume['name'], 'offline')
        self.driver._execute('volume', 'delete', volume['name'])
        self.mox.ReplayAll()
        self.driver.delete_volume(volume)

    def test_create_snapshot(self):
        snapshot = {'name': 'fakesnap', 'volume_name': 'fakevolume_name'}
        snap_name = 'fake_snap_name'
        self.driver._execute('volume', 'select', snapshot['volume_name'],
                             'snapshot', 'create-now').\
            AndReturn(['Snapshot name is %s' % snap_name])
        self.driver._execute('volume', 'select', snapshot['volume_name'],
                             'snapshot', 'rename', snap_name,
                             snapshot['name'])
        self.mox.ReplayAll()
        self.driver.create_snapshot(snapshot)

    def test_create_volume_from_snapshot(self):
        snapshot = {'name': 'fakesnap', 'volume_name': 'fakevolume_name'}
        volume = {'name': self.volume_name}
        self.driver._execute('volume', 'select', snapshot['volume_name'],
                             'snapshot', 'select', snapshot['name'], 'clone',
                             volume['name']).\
            AndReturn(['iSCSI target name is %s.' % self.fake_iqn])
        self.mox.ReplayAll()
        model_update = self.driver.create_volume_from_snapshot(volume,
                                                               snapshot)
        self.assertEqual(model_update, self._model_update)

    def test_create_cloned_volume(self):
        src_vref = {'id': 'fake_uuid'}
        volume = {'name': self.volume_name}
        src_volume_name = FLAGS.volume_name_template % src_vref['id']
        self.driver._execute('volume', 'select', src_volume_name, 'clone',
                             volume['name']).\
            AndReturn(['iSCSI target name is %s.' % self.fake_iqn])
        self.mox.ReplayAll()
        model_update = self.driver.create_cloned_volume(volume, src_vref)
        self.assertEqual(model_update, self._model_update)

    def test_delete_snapshot(self):
        snapshot = {'name': 'fakesnap', 'volume_name': 'fakevolume_name'}
        self.driver._execute('volume', 'select', snapshot['volume_name'],
                             'snapshot', 'delete', snapshot['name'])
        self.mox.ReplayAll()
        self.driver.delete_snapshot(snapshot)

    def test_initialize_connection(self):
        volume = {'name': self.volume_name}
        self.stubs.Set(self.driver, "_get_iscsi_properties",
                       self._fake_get_iscsi_properties)
        self.driver._execute('volume', 'select', volume['name'], 'access',
                             'create', 'initiator',
                             self.connector['initiator'], 'authmethod chap',
                             'username', FLAGS.eqlx_chap_login)
        self.mox.ReplayAll()
        iscsi_properties = self.driver.initialize_connection(volume,
                                                             self.connector)
        self.assertEqual(iscsi_properties['data'],
                         self._fake_get_iscsi_properties(volume))

    def test_terminate_connection(self):
        volume = {'name': self.volume_name}
        self.driver._execute('volume', 'select', volume['name'], 'access',
                             'delete', '1')
        self.mox.ReplayAll()
        self.driver.terminate_connection(volume, self.connector)

    def test_do_setup(self):
        fake_group_ip = '10.1.2.3'
        for feature in ('confirmation', 'paging', 'events', 'formatoutput'):
            self.driver._execute('cli-settings', feature, 'off')
        self.driver._execute('grpparams', 'show').\
            AndReturn(['Group-Ipaddress: %s' % fake_group_ip])
        self.mox.ReplayAll()
        self.driver.do_setup(self._context)
        self.assertEqual(fake_group_ip, self.driver._group_ip)

    def test_update_volume_status(self):
        self.driver._execute('pool', 'select', flags.FLAGS.eqlx_pool, 'show').\
            AndReturn(['TotalCapacity: 111GB', 'FreeSpace: 11GB'])
        self.mox.ReplayAll()
        self.driver._update_volume_status()
        self.assertEqual(self.driver._stats['total_capacity_gb'], 111.0)
        self.assertEqual(self.driver._stats['free_capacity_gb'], 11.0)

    def test_get_space_in_gb(self):
        self.assertEqual(self.driver._get_space_in_gb('123.0GB'), 123.0)
        self.assertEqual(self.driver._get_space_in_gb('123.0TB'), 123.0 * 1024)
        self.assertEqual(self.driver._get_space_in_gb('1024.0MB'), 1.0)

    def test_get_output(self):

        def _fake_recv(ignore_arg):
            return '%s> ' % FLAGS.eqlx_group_name

        chan = self.mox.CreateMock(paramiko.Channel)
        self.stubs.Set(chan, "recv", _fake_recv)
        self.assertEqual(self.driver._get_output(chan), [_fake_recv(None)])

    def test_get_prefixed_value(self):
        lines = ['Line1 passed', 'Line1 failed']
        prefix = ['Line1', 'Line2']
        expected_output = [' passed', None]
        self.assertEqual(self.driver._get_prefixed_value(lines, prefix[0]),
                         expected_output[0])
        self.assertEqual(self.driver._get_prefixed_value(lines, prefix[1]),
                         expected_output[1])
