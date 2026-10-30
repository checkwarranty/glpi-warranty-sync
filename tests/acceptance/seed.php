<?php
/** Run only in the isolated acceptance compose project. */
define('PLUGIN_CHECKWARRANTY_TESTING', true);
if (is_file('/var/www/glpi/src/Glpi/Kernel/Kernel.php')) {
    require '/var/www/glpi/vendor/autoload.php';
    $kernel = new \Glpi\Kernel\Kernel();
    $kernel->boot();
} else {
    require '/var/www/glpi/inc/includes.php';
}

$auth = new Auth();
$auth->login('glpi', 'glpi');
if (!$auth->auth_succeded) { throw new RuntimeException('Acceptance login failed'); }
Config::setConfigurationValues('core', ['enable_api' => 1, 'enable_api_login_external_token' => 1]);
$user = new User();
$user->update(['id' => Session::getLoginUserID(), '_reset_api_token' => 1]);
$user->getFromDB(Session::getLoginUserID());
$token = (new GLPIKey())->decrypt($user->fields['api_token']);
$appToken = Toolbox::getRandomString(40);
$client = new APIClient();
$client->add(['name' => 'Acceptance only', 'is_active' => 1, 'app_token' => $appToken]);
$ids = [];
foreach (['ENDONLY', 'FULL', 'NOTFOUND', 'SPECS', 'ASYNC'] as $kind) {
    $computer = new Computer();
    $id = $computer->add(['name' => 'Acceptance ' . $kind, 'serial' => 'TEST-' . $kind, 'entities_id' => 0]);
    if (!$id) { throw new RuntimeException('Cannot seed Computer'); }
    (new Infocom())->add(['itemtype' => 'Computer', 'items_id' => $id, 'buy_date' => '2020-03-01', 'warranty_info' => 'Manual note']);
    $ids[$kind] = $id;
}
file_put_contents('/tmp/cw-glpi-cli-auth.json', json_encode(['user_token' => $token, 'app_token' => $appToken, 'ids' => $ids]));
echo 'Acceptance data seeded: ' . GLPI_VERSION . "\n";
