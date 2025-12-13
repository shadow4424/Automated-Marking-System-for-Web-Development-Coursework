<?php
// Simple handler to demonstrate server-side input handling for AMS smoke test
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $name = isset($_POST['name']) ? $_POST['name'] : 'Anonymous';
    echo 'Hello, ' . htmlspecialchars($name, ENT_QUOTES, 'UTF-8');
} else {
    echo 'Submit the form to see a greeting.';
}
