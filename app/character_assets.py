"""Character assets keep generation image/audio together; portraits are preview-only."""
from .media_types import media_type


def asset_kind(asset):
    return asset.get('media_type') or media_type(asset.get('filename', asset.get('original_name', ''))) or 'image'


def asset_filename(asset, kind):
    if asset_kind(asset) == 'character':
        if kind == 'image':
            return asset['filename']  # always the turnaround
        if kind == 'audio':
            return asset['audio_filename']
    elif asset_kind(asset) == kind:
        return asset['filename']
    raise ValueError(f"素材 {asset.get('original_name')} 不支援 {kind}")


def migrate_characters(project):
    groups = {}
    for asset in project.get('assets', []):
        if asset.get('character_template_id') and asset_kind(asset) != 'character':
            groups.setdefault(asset['character_template_id'], []).append(asset)
    aliases = {}
    for items in groups.values():
        image = next((a for a in items if a.get('original_name', '').endswith('turnaround.png')), None)
        voice = next((a for a in items if a.get('original_name', '').endswith('voice.wav')), None)
        head = next((a for a in items if a.get('original_name', '').endswith('headshot.png')), None)
        if not image or not voice:
            continue
        image.update(media_type='character', audio_filename=voice['filename'],
                     headshot_filename=head['filename'] if head else None,
                     original_name=image.get('character_name') or image['original_name'],
                     voice_line=voice.get('notes', ''))
        for asset in items:
            if asset is not image:
                aliases[asset['id']] = image['id']
    if not aliases:
        return False
    project['assets'] = [a for a in project['assets'] if a['id'] not in aliases]
    for segment in project.get('segments', []):
        binding = segment.get('template') or {}
        for key in ('first_frame_asset_id', 'last_frame_asset_id'):
            if binding.get(key) in aliases:
                binding[key] = aliases[binding[key]]
        binding['image_order'] = list(dict.fromkeys(aliases.get(x, x) for x in binding.get('image_order', [])))
        segment['reference_asset_ids'] = list(dict.fromkeys(aliases.get(x, x) for x in segment.get('reference_asset_ids', [])))
    return True
