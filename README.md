# wg-cop

A telegram bot to track rommate social credit score and, if prompted, exercise microauthority.

## configuration

- Copy `config.example.py` to `config.py` and replace the placeholder values with your real bot token and Telegram IDs. The repository keeps `config.py` out of version control, so your secrets stay local.
- Keep your personal `config.py` private. When you need to share settings, edit and commit `config.example.py` with anonymised values instead.

## how to use

- <p><b> Start</b> with obtaining a bot-token by messaging botfather on telegram and paste it to config.py. Run the maBot.py script somewhere persistently. Find the bot account on telegram and add it as a member to your household group chat.<br><br>You can interact from inside the group chat or dm the bot.<br><br>
- <b> Track expenses </b> that involve all members of a household or a subset thereof. You specify who paid how much and who's splitting while the bot is keeping the books. Entries via DM to the bot protect the group from congestion.<br><br>
- <b> Chore score </b> is then world's best system to ensure everybody is contributing without endless definitions and rotations of roles. It works by simply tracking minutes spent doing anything that is agreed to be a credible to the chore score. Each 15 mins gets 1 Point. Penalties are introduced for every full week that a member is trailing more than 4 points behind the leader. Penalties are cumulative i.e. persist until imbursed. It's up to the household to specify what constitutes the penalty. <br><br>Example: The penatly to be a case of beer, paid and brought by the offender. You slacked and now the cleanliest cohabitant has 1h more than you on their record, your bring a case for every full week that this gap exists. <br><br>You record the minutes spent after every chore and the bot keeps score and announces possible penalties in the household group chat every monday.
</p>

## advanced handling

- **Vacation Status**: Members can set their status to "vacating" using `/setstatus <name> vacating` when on vacation. When selecting expense splitters, vacating members appear at the bottom with "(vacating)" label as a reminder to only include them for long-term expenses. Switch back with `/setstatus <name> active`.

- The bot sends an "I'm alive" message every day to confirm it is running to the "bothandler user id", if one is specified in config.py.<br><br>
- The bot sends a copy of the database (json) to the "chronicler user id" if one specified in config.py<br><br>

