<?php
session_start();
$name = $_POST['name'] ?? '';
$email = $_POST['email'] ?? '';
$safeName = htmlspecialchars($name, ENT_QUOTES, 'UTF-8');
$safeEmail = htmlspecialchars($email, ENT_QUOTES, 'UTF-8');
echo "<p>Hello $safeName ($safeEmail)</p>";
if ($safeName) {
    print "<p>Thanks for submitting.</p>";
}
?>
