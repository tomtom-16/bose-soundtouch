"""Échange sur boucle locale avec le listener, sans contact direct avec les Bose."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


def listener_status(check=False, name=None):
    try:
        port = int(os.environ.get('BOSE_BUTTONS_PORT', '8078'))
        if not 1 <= port <= 65535:
            raise ValueError('Port invalide')
        path = '/check' if check else '/status'
        if check and name is not None:
            path += '?' + urllib.parse.urlencode({'name': name})
        request = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                                         method='POST' if check else 'GET')
        # Ignorer les éventuels proxies système pour cet échange local.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=8 if check else 1) as response:
            result = json.load(response)
        if not isinstance(result, dict) or not isinstance(result.get('speakers'), dict):
            raise ValueError('Réponse invalide')
        return result
    except (OSError, ValueError, urllib.error.URLError):
        return {'ok': True, 'available': False, 'speakers': {},
                'error': 'Service des boutons indisponible. Vérifiez que bose-buttons est démarré.'}
