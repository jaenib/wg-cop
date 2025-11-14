# Vacation Status Feature

## Overview
Members can now set their status to "vacating" (on vacation) to indicate they should not be added to shared expense splits, especially for short-term consumables. When selecting splitters for an expense, members with vacation status appear at the bottom of the list with "(vacating)" displayed next to their name, serving as a visual reminder.

## How to Use

### Set Vacation Status
Use the `/setstatus` command to toggle a member's status between "active" and "vacating".

**Usage:**
```
/setstatus <member_name> [active|vacating]
```

**Examples:**
```
/setstatus Janidputzä vacating
  → Sets Janidputzä to vacation status

/setstatus Janidputzä active
  → Sets Janidputzä back to active status

/setstatus Janidputzä
  → Toggles Janidputzä's status (active → vacating or vacating → active)
```

### Visual Behavior When Selecting Splitters
When adding an expense and selecting who the expense should be split with:

1. **Active members** appear first in normal formatting:
   ```
   [ ] General Guysan
   [ ] Nicci Lopez
   [ ] Thomath Sucker
   ```

2. **Vacating members** appear at the bottom with italic notation:
   ```
   [ ] Gjango Gmüeshole (vacating)
   [ ] Janidputzä (vacating)
   ```

This visual distinction helps you remember to only include them for long-term expenses and not daily consumables.

## Data Structure

Members are now stored as objects with two fields:

```json
{
  "name": "Janidputzä",
  "status": "active"  // or "vacating"
}
```

## Implementation Details

- The feature uses `status` field in member objects (defaults to "active" for backward compatibility)
- All member matching uses normalized names (case-insensitive) for consistency
- Old member data (simple strings) is automatically migrated to the new object format on first load
- The feature integrates seamlessly with all existing expense tracking and chore logging functionality

## Technical Notes

### Helper Functions Added
- `_get_member_name(member)` - Extract display name from member object or string
- `_get_member_status(member)` - Get status field (defaults to "active")
- `_member_to_dict(name)` - Convert string member name to object format
- `set_vacation_status()` - Telegram command handler for `/setstatus`

### Modified Functions
- `build_split_inline_kb()` - Reordered to show active members first, then vacating
- `load_data()` - Auto-migrates members from strings to objects
- `_normalise_member_name()` - Now handles both strings and member objects
- `_match_member_name()` - Works with both string and object formats
- `_match_member_UID()` - Returns member objects instead of strings

### Backward Compatibility
The system gracefully handles mixed formats (some members as strings, some as objects) and automatically converts them all to the new object format on load.
