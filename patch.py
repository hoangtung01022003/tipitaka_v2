import sys

with open('app/translator.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
in_func = False
for i, line in enumerate(lines):
    if line.startswith('def summarize_section_text('):
        in_func = True
        new_lines.append('_SUMMARY_CACHE = {}\n\n')
        new_lines.append(line)
        continue
    
    if in_func:
        if line.strip() == 'if not full_text.strip():':
            new_lines.append(line)
            new_lines.append('        return {"points": []}\n')
            new_lines.append('\n')
            new_lines.append('    import hashlib\n')
            new_lines.append('    cache_key = f"{hashlib.md5(full_text.encode()).hexdigest()}_{language}"\n')
            new_lines.append('    if cache_key in _SUMMARY_CACHE:\n')
            new_lines.append('        return _SUMMARY_CACHE[cache_key]\n\n')
            continue
        if line.strip() == 'return {"points": []}' and 'if not full_text.strip()' in lines[i-1]:
            continue # already added
            
        if line.strip() == 'data = json.loads(response.text)':
            new_lines.append(line)
            new_lines.append('                _SUMMARY_CACHE[cache_key] = data\n')
            continue
            
        if line.strip() == 'return {"points": []}' and 'data = json.loads' not in lines[i-2]:
            new_lines.append('            _SUMMARY_CACHE[cache_key] = {"points": []}\n')
            new_lines.append('            return _SUMMARY_CACHE[cache_key]\n')
            continue

    new_lines.append(line)

with open('app/translator.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Patched successfully')
