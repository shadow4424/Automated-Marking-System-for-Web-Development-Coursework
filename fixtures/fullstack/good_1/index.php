<?php
session_start();
$name = $_POST['name'] ?? '';
if ($name) {
    echo "Hello " . htmlspecialchars($name);
}
?>
