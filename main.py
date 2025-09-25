"""
MkDocs Macros plugin functions for content transformation
"""
import re
from typing import List, Tuple, Optional

# Configuration for debug output - set to False in production
DEBUG_CONTENT_TABS = False


def define_env(env):
    """
    This is the hook for defining variables, macros and filters
    - variables: the dictionary that contains the environment variables
    - macro: a decorator function, to declare a macro.
    """

    @env.macro
    def content_tabs(text: str) -> str:
        """
        Convert markdown comments to Material for MkDocs content tabs.

        Handles various edge cases:
        - Indented tab markers
        - Inconsistent indentation between TAB_START and TAB_END
        - Different heading levels (###, ####)
        - Malformed structures
        - Whitespace variations
        - Groups consecutive tabs based on proximity, not exact indentation

        Args:
            text: The markdown text to transform

        Returns:
            Transformed text with content tab syntax
        """

        # Step 1: Find all tab markers and their positions
        tab_markers = []

        # Find TAB_START markers with their indentation and position
        start_pattern = r'(\n?[ \t]*)<!--\s*TAB_START:([^-]+?)\s*-->'
        for match in re.finditer(start_pattern, text):
            full_indent = match.group(1)
            # Clean the indent - remove newline and keep only spaces/tabs
            indent = full_indent.lstrip('\n')
            tab_name = match.group(2).strip()
            start_pos = match.start()
            end_pos = match.end()
            if DEBUG_CONTENT_TABS:
                print(f"DEBUG: Found TAB_START '{tab_name}' at pos {start_pos} with indent '{repr(indent)}'")
            tab_markers.append({
                'type': 'start',
                'name': tab_name,
                'indent': indent,
                'start': start_pos,
                'end': end_pos,
                'match': match
            })

        # Find TAB_END markers
        end_pattern = r'(\n?[ \t]*)<!--\s*TAB_END\s*-->'
        for match in re.finditer(end_pattern, text):
            full_indent = match.group(1)
            # Clean the indent - remove newline and keep only spaces/tabs
            indent = full_indent.lstrip('\n')
            start_pos = match.start()
            end_pos = match.end()
            if DEBUG_CONTENT_TABS:
                print(f"DEBUG: Found TAB_END at pos {start_pos} with indent '{repr(indent)}'")
            tab_markers.append({
                'type': 'end',
                'indent': indent,
                'start': start_pos,
                'end': end_pos,
                'match': match
            })

        # Sort markers by position
        tab_markers.sort(key=lambda x: x['start'])

        # Step 2: Pair up TAB_START and TAB_END markers using stack-based matching
        tab_blocks = []
        start_stack = []

        if DEBUG_CONTENT_TABS:
            print(f"DEBUG: Total markers found: {len(tab_markers)}")

        for marker in tab_markers:
            if marker['type'] == 'start':
                start_stack.append(marker)
                if DEBUG_CONTENT_TABS:
                    print(f"DEBUG: Pushed {marker['name']} to stack")
            elif marker['type'] == 'end' and start_stack:
                start_marker = start_stack.pop()
                # Use the TAB_START marker's indentation as the canonical base indentation
                # This handles cases where TAB_END has different indentation
                base_indent = start_marker['indent']
                tab_blocks.append({
                    'start_marker': start_marker,
                    'end_marker': marker,
                    'start_pos': start_marker['start'],
                    'end_pos': marker['end'],
                    'base_indent': base_indent  # Canonical indentation for this tab block
                })
                if DEBUG_CONTENT_TABS:
                    print(f"DEBUG: Paired {start_marker['name']} with TAB_END, base_indent='{repr(base_indent)}'")
            elif marker['type'] == 'end':
                if DEBUG_CONTENT_TABS:
                    print("DEBUG: Found TAB_END but no matching TAB_START in stack")

        if DEBUG_CONTENT_TABS:
            print(f"DEBUG: Created {len(tab_blocks)} tab blocks")

        # Step 3: Group consecutive tab blocks based on proximity and context
        tab_groups = []
        current_group = []

        for i, block in enumerate(tab_blocks):
            base_indent = block['base_indent']

            if current_group:
                # Check if this block should be grouped with the previous ones
                prev_block = current_group[-1]
                prev_end = prev_block['end_pos']
                curr_start = block['start_pos']

                # Get content between the previous block and current block
                between_content = text[prev_end:curr_start]
                between_stripped = between_content.strip()

                # Normalize indentation for comparison (count leading spaces/tabs)
                prev_indent_level = len(prev_block['base_indent'])
                curr_indent_level = len(base_indent)
                indent_diff = abs(prev_indent_level - curr_indent_level)

                # Check for list item markers or headers that would indicate a new section
                has_list_marker = re.search(r'^[-*]\s', between_stripped)
                has_heading = between_stripped.startswith('#') or re.search(r'\n#{1,6}\s', between_content)

                # Group tabs if they are:
                # 1. At the same indentation level
                # 2. Have minimal content between them
                # 3. No list markers or headings between them
                # 4. Very close together (less than 20 chars for immediate siblings)
                should_group = (
                    indent_diff == 0 and  # Must be at same indentation
                    len(between_stripped) < 20 and  # Very minimal content between
                    not has_list_marker and  # No new list items
                    not has_heading  # No headings between
                )

                if should_group:
                    current_group.append(block)
                    if DEBUG_CONTENT_TABS:
                        print(f"DEBUG: Added block '{block['start_marker']['name']}' to current group (indent_diff={indent_diff}, between_len={len(between_stripped)})")
                else:
                    # Start new group
                    tab_groups.append(current_group)
                    current_group = [block]
                    if DEBUG_CONTENT_TABS:
                        print(f"DEBUG: Started new group with '{block['start_marker']['name']}' (indent_diff={indent_diff}, between_len={len(between_stripped)}, has_list={has_list_marker}, has_heading={has_heading})")
            else:
                # First block or starting new group
                current_group = [block]
                if DEBUG_CONTENT_TABS:
                    print(f"DEBUG: Started first group with '{block['start_marker']['name']}'")

        if current_group:
            tab_groups.append(current_group)

        if DEBUG_CONTENT_TABS:
            print(f"DEBUG: Created {len(tab_groups)} tab groups: {[len(group) for group in tab_groups]}")

        # Step 4: Process groups from end to start to avoid position shifts
        tab_groups.sort(key=lambda group: group[0]['start_pos'], reverse=True)

        for group_idx, group in enumerate(tab_groups):
            if DEBUG_CONTENT_TABS:
                print(f"DEBUG: Processing group {group_idx + 1} with {len(group)} tabs")

            # Process all tabs consistently using the same logic
            processed_group = _process_tab_group(text, group)

            # Replace the entire group span
            group_start = group[0]['start_pos']
            group_end = group[-1]['end_pos']
            text = text[:group_start] + processed_group + text[group_end:]

        if DEBUG_CONTENT_TABS:
            print(f"DEBUG: Final processing complete")

        # Debug: Show a sample of the transformed text around content tabs
        if DEBUG_CONTENT_TABS:
            sample_start = text.find('=== "')
            if sample_start != -1:
                # Show more context to see the full tab structure
                sample = text[sample_start:sample_start+400]
                print(f"DEBUG: Sample transformed text:\n{repr(sample)}")

                # Also show it formatted for easier reading
                print(f"DEBUG: Formatted sample:\n{sample[:400]}")
                print("DEBUG: ---END SAMPLE---")

        return text


def _process_tab_content(content: str, tab_name: str, base_indent: str) -> str:
    """
    Process the content between tab markers.

    Args:
        content: Raw content between markers
        tab_name: Name of the tab
        base_indent: Base indentation level

    Returns:
        Formatted tab content with proper indentation
    """

    # Clean up content
    content = content.strip()

    if not content:
        return f'{base_indent}=== "{tab_name}"\n\n'

    # Split into lines for processing
    lines = content.split('\n')
    processed_lines = []

    # Remove heading lines (any line starting with #)
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            processed_lines.append(line)
        elif not stripped:  # Keep empty lines
            processed_lines.append('')

    # Remove leading and trailing empty lines
    while processed_lines and not processed_lines[0].strip():
        processed_lines.pop(0)
    while processed_lines and not processed_lines[-1].strip():
        processed_lines.pop()

    if not processed_lines:
        return f'{base_indent}=== "{tab_name}"\n\n'

    # Determine the minimum indentation of non-empty lines
    min_indent = float('inf')
    for line in processed_lines:
        if line.strip():  # Non-empty line
            indent_level = len(line) - len(line.lstrip())
            min_indent = min(min_indent, indent_level)

    if min_indent == float('inf'):
        min_indent = 0

    # Remove common indentation and add tab indentation
    final_lines = []
    # Tab content must be indented exactly 4 spaces more than the tab declaration
    tab_content_indent = base_indent + '    '

    for line in processed_lines:
        if line.strip():  # Non-empty line
            # Remove the original indentation completely and apply consistent tab indentation
            clean_line = line.lstrip()
            indented_line = tab_content_indent + clean_line
            final_lines.append(indented_line)
            if DEBUG_CONTENT_TABS:
                print(f"DEBUG: Line '{clean_line}' -> '{indented_line}' (indent='{repr(tab_content_indent)}')")
        else:  # Empty line
            final_lines.append('')

    final_content = '\n'.join(final_lines)
    result = f'{base_indent}=== "{tab_name}"\n\n{final_content}\n'

    if DEBUG_CONTENT_TABS:
        print(f"DEBUG: Final tab result for '{tab_name}':")
        print(repr(result))

    return result


def _process_tab_group(text: str, tab_blocks: list) -> str:
    """
    Process a group of tab blocks together (handles both single tabs and groups consistently).

    Args:
        text: The full text
        tab_blocks: List of tab blocks in the group (can be single tab or multiple)

    Returns:
        Formatted tab group content with proper indentation
    """
    if not tab_blocks:
        return ""

    # Use the base indentation from the first block's TAB_START marker
    # This handles inconsistent indentation between TAB_START and TAB_END
    base_indent = tab_blocks[0]['base_indent']

    # Check if tabs are inside a list item (indented with 2 spaces)
    # If so, we need to break out of the list context for tabs to work
    in_list_item = base_indent == '  '

    tab_contents = []

    if DEBUG_CONTENT_TABS:
        print(f"DEBUG: Processing {len(tab_blocks)} tabs with base_indent='{repr(base_indent)}', in_list_item={in_list_item}")

    for i, block in enumerate(tab_blocks):
        start_marker = block['start_marker']
        end_marker = block['end_marker']

        # Extract content between markers
        content_start = start_marker['end']
        content_end = end_marker['start']
        content = text[content_start:content_end]

        if DEBUG_CONTENT_TABS:
            print(f"DEBUG: Processing tab '{start_marker['name']}' ({i+1}/{len(tab_blocks)})")
            print(f"DEBUG: Raw content length: {len(content)}")

        # Process the individual tab content
        # If in a list item, use empty base_indent to break out of list context
        effective_indent = '' if in_list_item else base_indent
        processed_tab = _process_tab_content(
            content,
            start_marker['name'],
            effective_indent
        )

        tab_contents.append(processed_tab)

    if len(tab_blocks) == 1:
        # Single tab - return as-is
        if DEBUG_CONTENT_TABS:
            print("DEBUG: Single tab, returning as-is")
        return tab_contents[0]
    else:
        # Multiple tabs - join them to form a tab group
        # Remove trailing newlines from individual tabs and join with double newlines
        # This ensures Material for MkDocs recognizes them as a single tab group
        if DEBUG_CONTENT_TABS:
            print(f"DEBUG: Multiple tabs ({len(tab_blocks)}), creating tab group")
        cleaned_tabs = [tab.rstrip() for tab in tab_contents]

        # If in list item, add a line break before tabs to break out of list
        if in_list_item:
            result = '\n\n' + '\n\n'.join(cleaned_tabs) + '\n'
        else:
            result = '\n\n'.join(cleaned_tabs) + '\n'

        if DEBUG_CONTENT_TABS:
            print(f"DEBUG: Tab group result preview: {repr(result[:200])}")
        return result


    @env.macro
    def include_external_md(url, transform_tabs=True):
        """
        Include external markdown content with optional tab transformation.

        Args:
            url: URL or file path to include
            transform_tabs: Whether to transform content tabs (default: True)

        Returns:
            The included content, optionally transformed
        """
        # This is a placeholder for future functionality
        return f"<!-- Include content from {url} -->"