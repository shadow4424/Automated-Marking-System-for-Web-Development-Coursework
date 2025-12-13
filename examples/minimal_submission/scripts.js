// Demo client-side API call for AMS smoke test
async function pingApi() {
  try {
    const response = await fetch('https://example.com/api/ping');
    if (!response.ok) {
      throw new Error('Ping failed');
    }
    return await response.text();
  } catch (error) {
    console.error('Ping error', error);
    return null;
  }
}

pingApi();
