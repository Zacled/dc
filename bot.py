"""Crypto-payment ticket bot for Discord.

Flow:
  1. Owner runs /panel to post a purchase panel with LTC / SOL / ETH buttons.
  2. A buyer clicks a coin -> a private ticket channel is opened for them.
  3. The bot quotes the live $-price in that coin (with a unique amount), shows
     the receiving address + a QR code, and starts watching the chain.
  4. When the payment confirms, the bot pings the owner in the ticket so they
     can deliver the product key by hand.
"""

import base64
import io
import json
import logging
import re

import aiohttp
import discord
import qrcode
from discord.ext import tasks

import config
import database as db
import payments
import prices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")


# --- Helpers ---------------------------------------------------------------
def format_amount(amount: float, decimals: int) -> str:
    """Exact, human-friendly amount string (no scientific notation)."""
    text = f"{amount:.{decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def payment_uri(coin: str, address: str, amount: float) -> str:
    """Build a wallet payment URI so QR scans can prefill the amount."""
    meta = config.COINS[coin]
    if coin == "ETH":
        wei = int(round(amount * 1e18))
        return f"ethereum:{address}?value={wei}"
    # Tokens (USDT/USDC) have no universal amount URI — QR just the address.
    if not meta.get("uri_scheme"):
        return address
    return f"{meta['uri_scheme']}:{address}?amount={amount:.{meta['decimals']}f}"


def make_qr_file(data: str, box_size: int = 6) -> discord.File:
    qr = qrcode.QRCode(box_size=box_size, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename="payment.png")


def sanitize_channel_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return cleaned[:90] or "buyer"


def resolve_mentions(text: str, guild) -> str:
    """Turn raw <@id>/<@&id>/<#id> mentions into readable @names / #names."""
    if not text or guild is None:
        return text

    def user(m):
        member = guild.get_member(int(m.group(1)))
        return f"@{member.display_name}" if member else m.group(0)

    def role(m):
        r = guild.get_role(int(m.group(1)))
        return f"@{r.name}" if r else m.group(0)

    def chan(m):
        c = guild.get_channel(int(m.group(1)))
        return f"#{c.name}" if c else m.group(0)

    text = re.sub(r"<@!?(\d+)>", user, text)
    text = re.sub(r"<@&(\d+)>", role, text)
    text = re.sub(r"<#(\d+)>", chan, text)
    return text


def is_staff_member(user) -> bool:
    """True if the user is the owner or has the staff/support role."""
    if user.id == config.OWNER_ID:
        return True
    role_id = config.SUPPORT_ROLE_ID or config.STAFF_ROLE_ID
    return bool(role_id and any(r.id == role_id for r in getattr(user, "roles", [])))


def decode_key(code: str) -> tuple[str, str]:
    """Pull (jti, label) out of a license code (base64url payload before the '.')."""
    payload_b64 = code.strip().split(".")[0]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    obj = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    return obj.get("jti", ""), obj.get("id", "")


# --- Views (persistent across restarts) ------------------------------------
COIN_EMOJI = {
    "LTC": "🪙", "SOL": "🟣", "ETH": "💎",
    "USDT_TRON": "💵", "USDT_SOL": "💵", "USDC_SOL": "🔵", "USDC_ETH": "🔵",
}
TICKER_EMOJI = {"USDT": "💵", "USDC": "🔵"}


class BuyButton(discord.ui.Button):
    def __init__(
        self, product: dict, coin: str, meta: dict, *, row: int | None, multi: bool,
        label_override: str | None = None,
    ) -> None:
        # With more than one product, show which product each button buys.
        if label_override:
            label = label_override
        elif multi:
            label = f"{product['name']} · {meta['name']}"
        else:
            label = f"Pay with {meta['name']}"
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"buy_{product['id']}_{coin.lower()}",
            emoji=COIN_EMOJI.get(coin),
            row=row,
        )
        self.coin = coin
        self.product = product

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.open_ticket(interaction, self.coin, self.product)


class StablecoinButton(discord.ui.Button):
    """Opens a sub-menu to pick the network for a stablecoin (USDT/USDC)."""

    def __init__(self, product: dict, ticker: str, *, row: int | None, multi: bool) -> None:
        label = f"{product['name']} · {ticker}" if multi else f"Pay with {ticker}"
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"buy_{product['id']}_{ticker.lower()}",
            emoji=TICKER_EMOJI.get(ticker),
            row=row,
        )
        self.product = product
        self.ticker = ticker

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.open_stable_menu(interaction, self.product, self.ticker)


class OtherButton(discord.ui.Button):
    def __init__(self, row: int | None = 1) -> None:
        super().__init__(
            label="Pay with another method",
            style=discord.ButtonStyle.secondary,
            custom_id="buy_other",
            emoji="💬",
            row=row,  # sits on its own row, below the coin buttons
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.open_other_ticket(interaction)


class PurchasePanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        enabled = config.enabled_coins()
        products = config.PRODUCTS
        multi = len(products) > 1
        # One row of payment buttons per product.
        for index, product in enumerate(products):
            row = min(index, 4)
            for coin in config.DIRECT_COINS:
                if coin in enabled:
                    self.add_item(
                        BuyButton(product, coin, enabled[coin], row=row, multi=multi)
                    )
            # A button per stablecoin group that has at least one network set up.
            for ticker in config.STABLE_GROUPS:
                if config.enabled_group_networks(ticker):
                    self.add_item(StablecoinButton(product, ticker, row=row, multi=multi))
        # A catch-all "other payment method" ticket below the coin buttons.
        self.add_item(OtherButton(row=min(len(products), 4)))


class TicketControls(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Check payment now",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_check",
        emoji="🔄",
    )
    async def check(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        order = db.get_order_by_channel(interaction.channel_id)
        if order is None:
            await interaction.followup.send("No order is linked to this ticket.", ephemeral=True)
            return
        if order["status"] == "paid":
            await interaction.followup.send("This order is already marked paid. ✅", ephemeral=True)
            return

        await interaction.client.run_scan()
        order = db.get_order(order["id"])
        messages = {
            "pending": "No payment seen yet. Send the exact amount and try again in a minute.",
            "detected": "Payment seen — waiting for confirmations. Hang tight.",
            "paid": "Payment confirmed! ✅ The owner has been pinged.",
            "expired": "This order expired. Please open a new ticket.",
        }
        await interaction.followup.send(
            messages.get(order["status"], "Still waiting…"), ephemeral=True
        )

    @discord.ui.button(
        label="Close ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close",
        emoji="🔒",
    )
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        order = db.get_order_by_channel(interaction.channel_id)
        is_staff = interaction.user.id == config.OWNER_ID or (
            config.STAFF_ROLE_ID
            and any(r.id == config.STAFF_ROLE_ID for r in getattr(interaction.user, "roles", []))
        )
        is_buyer = order is not None and interaction.user.id == order["user_id"]
        if not (is_staff or is_buyer):
            await interaction.response.send_message(
                "Only the buyer or staff can close this ticket.", ephemeral=True
            )
            return

        # Once a payment has been sent/confirmed, don't let the buyer close the
        # ticket — they could accidentally close it before getting their item.
        # Staff can still close it (after delivering).
        if (
            is_buyer
            and not is_staff
            and order is not None
            and order["status"] in ("detected", "paid")
        ):
            await interaction.response.send_message(
                "💸 Your payment is being processed — please **wait for staff** to "
                "deliver your item before closing. A staff member will close this "
                "ticket once you're sorted.",
                ephemeral=True,
            )
            return

        if order is not None and order["status"] in ("pending", "detected"):
            db.set_status(order["id"], "cancelled")
        await interaction.response.send_message("Saving transcript and closing…")
        opener_id = order["user_id"] if order is not None else None
        await interaction.client._post_transcript(
            interaction.channel,
            interaction.user,
            opener_id=opener_id,
            dest_id=config.PURCHASE_TRANSCRIPT_CHANNEL_ID,
            dest_name=config.PURCHASE_TRANSCRIPT_CHANNEL_NAME,
        )
        await interaction.channel.delete(reason="Ticket closed")


# --- Support ticket views --------------------------------------------------
class ReasonButton(discord.ui.Button):
    def __init__(self, index: int, reason: str) -> None:
        super().__init__(
            label=reason[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"support_reason_{index}",
            row=index // 5,  # up to 5 buttons per row
        )
        self.reason = reason

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.open_support_ticket(interaction, self.reason)


class SupportPanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        # One button per reason (Discord allows up to 25 buttons / 5 rows).
        for index, reason in enumerate(config.SUPPORT_REASONS[:25]):
            self.add_item(ReasonButton(index, reason))


class SupportTicketControls(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close ticket",
        style=discord.ButtonStyle.danger,
        custom_id="support_close",
        emoji="🔒",
    )
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        ticket = db.get_support_ticket_by_channel(interaction.channel_id)
        is_staff = interaction.user.id == config.OWNER_ID or (
            config.SUPPORT_ROLE_ID
            and any(r.id == config.SUPPORT_ROLE_ID for r in getattr(interaction.user, "roles", []))
        )
        is_opener = ticket is not None and interaction.user.id == ticket["user_id"]
        if not (is_staff or is_opener):
            await interaction.response.send_message(
                "Only the person who opened this ticket or staff can close it.",
                ephemeral=True,
            )
            return
        if ticket is not None:
            db.set_support_status(ticket["id"], "closed")
        await interaction.response.send_message("Saving transcript and closing…")
        opener_id = ticket["user_id"] if ticket is not None else None
        await interaction.client._post_transcript(
            interaction.channel,
            interaction.user,
            opener_id=opener_id,
            dest_id=config.SUPPORT_TRANSCRIPT_CHANNEL_ID,
            dest_name=config.SUPPORT_TRANSCRIPT_CHANNEL_NAME,
        )
        await interaction.channel.delete(reason="Support ticket closed")


class TranscriptControls(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Reopen ticket",
        style=discord.ButtonStyle.success,
        custom_id="transcript_reopen",
        emoji="🔓",
    )
    async def reopen(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not is_staff_member(interaction.user):
            await interaction.response.send_message(
                "Only staff can reopen a ticket.", ephemeral=True
            )
            return
        await interaction.client.reopen_from_transcript(interaction)


# --- Bot -------------------------------------------------------------------
class PaymentBot(discord.Client):
    def __init__(self, *, privileged: bool = True) -> None:
        intents = discord.Intents.default()
        # message_content: read what users type (ticket transcripts).
        # members: receive join/leave events (invite tracking).
        # Both must also be enabled under Developer Portal -> Bot -> Privileged Intents.
        intents.message_content = privileged
        intents.members = privileged
        super().__init__(intents=intents)
        self.tree = discord.app_commands.CommandTree(self)
        self.session: aiohttp.ClientSession | None = None
        # guild_id -> {invite_code: uses}, for detecting which invite was used.
        self.invite_cache: dict[int, dict[str, int]] = {}

    async def setup_hook(self) -> None:
        db.init_db()
        self.session = aiohttp.ClientSession()

        # Register persistent views so buttons keep working after a restart.
        self.add_view(PurchasePanel())
        self.add_view(TicketControls())
        self.add_view(SupportPanel())
        self.add_view(SupportTicketControls())
        self.add_view(TranscriptControls())

        self.tree.add_command(panel_command)
        self.tree.add_command(support_panel_command)
        self.tree.add_command(linkkey_command)
        if config.GUILD_ID:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

        self.payment_watcher.start()
        if config.PANTRY_ID:
            self.warning_watcher.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id)
        enabled = ", ".join(config.enabled_coins()) or "NONE (set addresses in .env!)"
        log.info("Accepting: %s", enabled)
        log.info(
            "Support role to ping: %s",
            config.SUPPORT_ROLE_ID or "NONE (set STAFF_ROLE_ID; will ping owner)",
        )
        await self._cache_all_invites()
        # Diagnostics so the logs reveal exactly what invite tracking is missing.
        guild = self.guilds[0] if self.guilds else None
        ch = self._invite_log_channel(guild) if guild else None
        log.info(
            "Invite tracking -> members_intent=%s | log_channel=%s | invites_readable=%s",
            self.intents.members,
            f"#{ch.name}" if ch else f"NOT FOUND (looking for '{config.INVITE_LOG_CHANNEL_NAME}')",
            bool(guild and guild.id in self.invite_cache),
        )

    # --- Invite tracking ---------------------------------------------------
    async def _cache_all_invites(self) -> None:
        for guild in self.guilds:
            try:
                invites = await guild.invites()
                self.invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
            except discord.Forbidden:
                log.warning(
                    "Missing 'Manage Server' permission in %s — invite tracking off there.",
                    guild.name,
                )
            except discord.HTTPException:
                pass

    def _invite_log_channel(self, guild):
        if config.INVITE_LOG_CHANNEL_ID:
            ch = guild.get_channel(config.INVITE_LOG_CHANNEL_ID)
            if ch:
                return ch
        return discord.utils.get(guild.text_channels, name=config.INVITE_LOG_CHANNEL_NAME)

    async def on_invite_create(self, invite: discord.Invite) -> None:
        self.invite_cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0

    async def on_invite_delete(self, invite: discord.Invite) -> None:
        self.invite_cache.get(invite.guild.id, {}).pop(invite.code, None)

    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild

        # Auto-role: give every new member the configured role.
        if config.AUTO_ROLE_ID:
            role = guild.get_role(config.AUTO_ROLE_ID)
            if role is not None:
                try:
                    await member.add_roles(role, reason="Auto-role on join")
                except discord.Forbidden:
                    log.warning(
                        "Can't assign auto-role in %s — need Manage Roles and the "
                        "bot's role above @%s.", guild.name, role.name,
                    )
                except discord.HTTPException:
                    pass

        inviter = None
        try:
            current = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            current = None

        if current is not None:
            cached = self.invite_cache.get(guild.id, {})
            for inv in current:
                if (inv.uses or 0) > cached.get(inv.code, 0):
                    inviter = inv.inviter
                    break
            self.invite_cache[guild.id] = {inv.code: inv.uses or 0 for inv in current}

        channel = self._invite_log_channel(guild)
        if inviter is not None:
            db.record_invite(guild.id, member.id, inviter.id, inviter.name)
            count = db.count_invites(guild.id, inviter.id)
            text = (
                f"📥 {member.mention} has been invited by **{inviter.name}** "
                f"and now has **{count}** invite{'s' if count != 1 else ''}."
            )
        else:
            db.record_invite(guild.id, member.id, 0, "unknown")
            text = f"📥 {member.mention} joined — couldn't tell who invited them."

        if channel is not None:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=False)
            )

    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        channel = self._invite_log_channel(guild)
        if channel is None:
            return
        rec = db.get_invite_record(guild.id, member.id)
        if rec and rec["inviter_id"]:
            text = (
                f"📤 **{member.name}** left. They were invited by "
                f"**{rec['inviter_name']}**."
            )
        else:
            text = f"📤 **{member.name}** left."
        await channel.send(text, allowed_mentions=discord.AllowedMentions(users=False))

    # --- Ticket creation ---------------------------------------------------
    async def _create_ticket_channel(self, guild, user, *, prefix: str, reason: str):
        """Create a private channel visible to the buyer, staff, and the bot."""
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }
        if config.STAFF_ROLE_ID:
            staff_role = guild.get_role(config.STAFF_ROLE_ID)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )

        category = (
            guild.get_channel(config.TICKET_CATEGORY_ID)
            if config.TICKET_CATEGORY_ID
            else None
        )
        return await guild.create_text_channel(
            name=sanitize_channel_name(f"{prefix}-{user.name}"),
            overwrites=overwrites,
            category=category,
            reason=reason,
        )

    async def _post_transcript(
        self,
        channel: discord.TextChannel,
        closed_by,
        *,
        opener_id: int | None = None,
        dest_id: int = 0,
        dest_name: str = "",
    ) -> None:
        """Save a ticket's messages to the given transcript channel before deletion."""
        dest = None
        if dest_id:
            dest = self.get_channel(dest_id)
        if dest is None and dest_name and channel.guild is not None:
            dest = discord.utils.get(channel.guild.text_channels, name=dest_name)
        if dest is None:
            return  # no transcript channel configured/found

        guild = channel.guild
        entries: list[tuple[str, str, str]] = []  # (timestamp, author, text)
        try:
            async for msg in channel.history(limit=1000, oldest_first=True):
                ts = msg.created_at.strftime("%b %d, %Y %I:%M %p")
                text = msg.clean_content or ""
                for embed in msg.embeds:
                    parts = [p for p in (embed.title, embed.description) if p]
                    if parts:
                        text += ("\n" if text else "") + resolve_mentions(
                            "\n".join(parts), guild
                        )
                for att in msg.attachments:
                    text += ("\n" if text else "") + f"[attachment] {att.url}"
                entries.append((ts, msg.author.display_name, text))
        except discord.HTTPException:
            return

        # Readable conversation, posted inline (no file to open).
        blocks = [f"**{author}**  ·  *{ts}*\n{text}" for ts, author, text in entries]
        conversation = "\n\n".join(blocks) or "(no messages)"
        if len(conversation) > 4000:
            conversation = conversation[:4000].rstrip() + "\n\n… *(older messages trimmed)*"

        embed = discord.Embed(
            title=f"📑 Transcript — #{channel.name}",
            description=conversation,
            color=0x99AAB5,
        )
        embed.add_field(name="Closed by", value=str(closed_by), inline=True)
        embed.add_field(name="Messages", value=str(len(entries)), inline=True)
        # Encode the opener so the Reopen button knows who to recreate it for.
        view = None
        if opener_id:
            embed.set_footer(text=f"opener:{opener_id}")
            view = TranscriptControls()
        try:
            await dest.send(embed=embed, view=view)
        except discord.HTTPException:
            log.warning("Couldn't post transcript to #%s", dest.name)

    async def reopen_from_transcript(self, interaction: discord.Interaction) -> None:
        """Recreate a ticket for the original opener and re-attach the transcript."""
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        # The opener id is stashed in the transcript embed's footer.
        opener_id = None
        if interaction.message.embeds:
            footer = interaction.message.embeds[0].footer.text or ""
            if footer.startswith("opener:"):
                opener_id = int(footer.split(":", 1)[1])
        if not opener_id:
            await interaction.followup.send(
                "Couldn't find who this ticket belonged to.", ephemeral=True
            )
            return

        # get_member only sees cached members (we don't run the members intent),
        # so fall back to a direct API fetch before giving up.
        member = guild.get_member(opener_id)
        if member is None:
            try:
                member = await guild.fetch_member(opener_id)
            except discord.NotFound:
                await interaction.followup.send(
                    "That member is no longer in the server.", ephemeral=True
                )
                return
            except discord.HTTPException:
                await interaction.followup.send(
                    "Couldn't look up that member right now — try again.", ephemeral=True
                )
                return

        channel = await self._create_ticket_channel(
            guild, member, prefix="reopened",
            reason=f"Ticket reopened by {interaction.user}",
        )
        db.create_support_ticket(member.id, channel.id, "Reopened")

        ping = f"<@&{config.SUPPORT_ROLE_ID}>" if config.SUPPORT_ROLE_ID else ""
        embed = discord.Embed(
            title="🔓 Ticket reopened",
            description=(
                f"This ticket was reopened by {interaction.user.mention}. The previous "
                "conversation is shown below."
            ),
            color=0x2ECC71,
        )
        await channel.send(
            content=f"{member.mention} {ping}".strip(),
            embed=embed,
            view=SupportTicketControls(),
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=[discord.Object(id=config.SUPPORT_ROLE_ID)] if config.SUPPORT_ROLE_ID else False,
            ),
        )
        # Bring the saved conversation along by copying it from the transcript.
        if interaction.message.embeds:
            prev = interaction.message.embeds[0].description
            if prev:
                await channel.send(
                    embed=discord.Embed(
                        title="📜 Previous conversation", description=prev[:4096], color=0x99AAB5
                    )
                )
        await interaction.followup.send(
            f"Reopened: {channel.mention}", ephemeral=True
        )

    async def open_stable_menu(
        self, interaction: discord.Interaction, product: dict, ticker: str
    ) -> None:
        """Show a sub-menu to choose the network for a stablecoin (USDT/USDC)."""
        networks = config.enabled_group_networks(ticker)
        if not networks:
            await interaction.response.send_message(
                f"{ticker} isn't set up yet. Ping staff.", ephemeral=True
            )
            return
        view = discord.ui.View(timeout=180)
        for code, meta in networks.items():
            view.add_item(
                BuyButton(
                    product, code, meta, row=None, multi=False,
                    label_override=config.NETWORK_NAMES.get(code, meta["name"]),
                )
            )
        await interaction.response.send_message(
            f"Which network do you want to pay **{ticker}** on?",
            view=view,
            ephemeral=True,
        )

    async def open_other_ticket(self, interaction: discord.Interaction) -> None:
        """Open a manual ticket for a non-crypto / 'other' payment method."""
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        # Reuse an existing open 'other' ticket instead of spamming new ones.
        for order in db.get_active_orders():
            if order["user_id"] == user.id and order["coin"] == "OTHER":
                channel = self.get_channel(order["channel_id"])
                if channel is not None:
                    await interaction.followup.send(
                        f"You already have an open ticket: {channel.mention}",
                        ephemeral=True,
                    )
                    return

        channel = await self._create_ticket_channel(
            guild, user, prefix="other", reason=f"Other-payment ticket for {user}"
        )
        # Minimal order row so the buyer can close it and we don't reopen dupes.
        db.create_order(
            user.id, channel.id, "OTHER", "", config.PRODUCT_PRICE_USD, 0.0
        )

        embed = discord.Embed(
            title=f"💬 {config.PRODUCT_NAME} — Other payment method",
            description=(
                "Thanks! You've opened a ticket to pay another way. "
                "Staff will be with you shortly to arrange payment and delivery."
            ),
            color=0x5865F2,
        )
        embed.add_field(name="Price", value=f"${config.PRODUCT_PRICE_USD:.2f} USD", inline=True)
        owner = f"<@{config.OWNER_ID}>" if config.OWNER_ID else ""
        await channel.send(
            content=f"{user.mention} {owner}".strip(),
            embed=embed,
            view=TicketControls(),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await interaction.followup.send(
            f"Your ticket is ready: {channel.mention}", ephemeral=True
        )

    async def open_support_ticket(
        self, interaction: discord.Interaction, reason: str
    ) -> None:
        """Open a private support ticket and ping the support/staff role."""
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        # One open support ticket per user.
        for ticket in db.get_open_support_tickets():
            if ticket["user_id"] == user.id:
                channel = self.get_channel(ticket["channel_id"])
                if channel is not None:
                    await interaction.followup.send(
                        f"You already have an open support ticket: {channel.mention}",
                        ephemeral=True,
                    )
                    return

        channel = await self._create_ticket_channel(
            guild, user, prefix="support", reason=f"Support ticket for {user}"
        )
        ticket_id = db.create_support_ticket(user.id, channel.id, reason)

        # Explicitly whitelist the specific role so the bot can ping it even if
        # the role isn't set to "mentionable" (and without needing the
        # Mention-All-Roles permission).
        allowed = discord.AllowedMentions(users=True)
        if config.SUPPORT_ROLE_ID:
            ping = f"<@&{config.SUPPORT_ROLE_ID}>"
            allowed = discord.AllowedMentions(
                users=True, roles=[discord.Object(id=config.SUPPORT_ROLE_ID)]
            )
        elif config.OWNER_ID:
            ping = f"<@{config.OWNER_ID}>"
        else:
            ping = ""

        embed = discord.Embed(
            title=f"🎫 Support Ticket #{ticket_id}",
            description=(
                f"**Reason:** {reason}\n\n"
                f"Thanks {user.mention}! Staff have been notified and will be with "
                "you shortly. Go ahead and describe your issue in detail."
            ),
            color=0x5865F2,
        )
        await channel.send(
            content=f"{user.mention} {ping}".strip(),
            embed=embed,
            view=SupportTicketControls(),
            allowed_mentions=allowed,
        )
        await interaction.followup.send(
            f"Your support ticket is open: {channel.mention}", ephemeral=True
        )

    async def open_ticket(
        self, interaction: discord.Interaction, coin: str, product: dict
    ) -> None:
        meta = config.COINS[coin]
        if not meta["address"]:
            await interaction.response.send_message(
                f"{meta['name']} payments aren't set up yet. Ping staff.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user
        price_usd = product["price_usd"]

        # Reuse an existing open ticket for the same product+coin (no spamming).
        for order in db.get_active_orders():
            if (
                order["user_id"] == user.id
                and order["coin"] == coin
                and order["product_name"] == product["name"]
            ):
                channel = self.get_channel(order["channel_id"])
                if channel is not None:
                    await interaction.followup.send(
                        f"You already have an open {coin} ticket for "
                        f"{product['name']}: {channel.mention}",
                        ephemeral=True,
                    )
                    return

        # Quote the live price.
        try:
            coin_amount, coin_price = await prices.usd_to_coin(
                self.session, meta["coingecko_id"], price_usd
            )
        except prices.PriceError as exc:
            await interaction.followup.send(
                f"Couldn't fetch the {coin} price right now: {exc}", ephemeral=True
            )
            return

        expected = payments.make_unique_amount(coin, coin_amount)

        channel = await self._create_ticket_channel(
            guild, user, prefix=f"{product['id']}-{coin}",
            reason=f"{product['name']} order ({coin}) for {user}",
        )

        order_id = db.create_order(
            user.id, channel.id, coin, meta["address"], price_usd, expected,
            product["name"],
        )

        ticker = meta["ticker"]
        amount_str = format_amount(expected, meta["decimals"])
        embed = discord.Embed(
            title=f"🧾 Order #{order_id} — {product['name']}",
            description=f"Pay in **{meta['name']}**, follow the instructions below.",
            color=meta["color"],
        )
        embed.add_field(name="Price", value=f"${price_usd:.2f} USD", inline=True)
        embed.add_field(name=f"1 {ticker}", value=f"≈ ${coin_price:,.2f}", inline=True)
        embed.add_field(name="​", value="​", inline=True)
        embed.add_field(
            name=f"💰 Amount to send ({ticker})",
            value=f"**SEND EXACTLY THIS AMOUNT** — copy it from the message below.\n```\n{amount_str}\n```",
            inline=False,
        )
        embed.add_field(
            name="📬 To this address",
            value=f"Copy it from the message below.\n```\n{meta['address']}\n```",
            inline=False,
        )
        embed.add_field(
            name="What happens next",
            value=(
                f"The bot is now watching for your **{meta['name']}** payment. Once it "
                f"confirms, staff are pinged to deliver your key. Use "
                "**Check payment now** to refresh."
            ),
            inline=False,
        )
        embed.set_thumbnail(url="attachment://payment.png")  # small QR, top-right
        embed.set_footer(
            text=f"🔍 Tap the QR to enlarge · Expires in {config.ORDER_EXPIRY_MINUTES} min · Order #{order_id}"
        )

        qr = make_qr_file(payment_uri(coin, meta["address"], expected))
        await channel.send(
            content=user.mention, embed=embed, file=qr, view=TicketControls()
        )
        # Plain, single-value messages so mobile users can long-press -> Copy Text
        # and get exactly the amount / address (embeds aren't copyable on mobile).
        await channel.send("⬇️ **Exact amount** (long-press → Copy Text)")
        await channel.send(amount_str)
        await channel.send("⬇️ **Address** (long-press → Copy Text)")
        await channel.send(meta["address"])
        await interaction.followup.send(
            f"Your {meta['name']} ticket is ready: {channel.mention}", ephemeral=True
        )

    # --- Payment watching --------------------------------------------------
    async def run_scan(self) -> None:
        if self.session is None:
            return
        await payments.scan_once(
            self.session,
            on_detected=self._on_detected,
            on_confirmed=self._on_confirmed,
            on_expired=self._on_expired,
        )

    async def _on_detected(self, order, payment) -> None:
        channel = self.get_channel(order["channel_id"])
        if channel is None:
            return
        meta = config.COINS[order["coin"]]
        embed = discord.Embed(
            title="⏳ Payment detected — confirming",
            description=(
                f"Saw **{format_amount(payment['amount'], meta['decimals'])} "
                f"{meta['ticker']}** ({payment['confirmations']}/{meta['min_conf']} "
                "confirmations). You'll be all set once it confirms."
            ),
            color=0xF1C40F,
        )
        await channel.send(embed=embed)

    async def _on_confirmed(self, order, payment) -> None:
        channel = self.get_channel(order["channel_id"])
        if channel is None:
            return
        meta = config.COINS[order["coin"]]
        embed = discord.Embed(
            title="✅ Payment confirmed!",
            description=(
                f"Order **#{order['id']}** for **{order['product_name'] or config.PRODUCT_NAME}** is paid.\n"
                f"Amount: `{format_amount(payment['amount'], meta['decimals'])} "
                f"{meta['ticker']}`\nTx: `{payment['txid']}`"
            ),
            color=0x2ECC71,
        )
        owner = f"<@{config.OWNER_ID}>" if config.OWNER_ID else ""
        await channel.send(
            content=f"{owner} payment received — please deliver the key. "
            f"<@{order['user_id']}> you're all set!",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def _on_expired(self, order, _payment) -> None:
        channel = self.get_channel(order["channel_id"])
        if channel is None:
            return
        embed = discord.Embed(
            title="⌛ Order expired",
            description=(
                "No payment was detected in time. If you've already paid, ping "
                "staff with your transaction ID. Otherwise open a new ticket."
            ),
            color=0xE74C3C,
        )
        await channel.send(embed=embed)

    @tasks.loop(seconds=config.POLL_INTERVAL_SECONDS)
    async def payment_watcher(self) -> None:
        try:
            await self.run_scan()
        except Exception:  # noqa: BLE001 - keep the loop alive no matter what
            log.exception("payment_watcher pass failed")

    @payment_watcher.before_loop
    async def _before_watcher(self) -> None:
        await self.wait_until_ready()

    # --- Key-sharing warning watcher ---------------------------------------
    def _warnings_channel(self, guild_id: int | None):
        if config.WARNINGS_CHANNEL_ID:
            ch = self.get_channel(config.WARNINGS_CHANNEL_ID)
            if ch:
                return ch
        guilds = [self.get_guild(guild_id)] if guild_id else self.guilds
        for g in guilds:
            if g:
                ch = discord.utils.get(g.text_channels, name=config.WARNINGS_CHANNEL_NAME)
                if ch:
                    return ch
        return None

    async def check_warnings(self) -> None:
        if not config.PANTRY_ID or self.session is None:
            return
        links = db.get_key_links()
        if not links:
            return
        url = f"https://getpantry.cloud/apiv1/pantry/{config.PANTRY_ID}/basket/wpbind"
        try:
            async with self.session.get(
                url, headers={"Content-Type": "application/json"}, timeout=30
            ) as resp:
                if resp.status != 200:
                    return  # 400 = basket not created yet; ignore
                bindings = await resp.json()
        except Exception:  # noqa: BLE001
            return

        for link in links:
            entry = bindings.get(link["jti"]) if isinstance(bindings, dict) else None
            w = entry.get("w", 0) if isinstance(entry, dict) else 0
            if w > link["last_warn"]:
                await self._send_warning(link, w)
                db.set_key_last_warn(link["jti"], w)

    async def _send_warning(self, link, w: int) -> None:
        level = min(w, 3)
        name = link["label"] or "your key"
        if level >= 3:
            dm = (
                "🚫 **Warning 3/3 — final.** Your key was used on too many other "
                "computers and has been **automatically revoked**. If you think this "
                "is a mistake, open a support ticket."
            )
        elif level == 2:
            dm = (
                "⚠️ **Warning 2/3.** Your key is locked to one computer and was used "
                "on another device again. **One more and it gets revoked** — do not "
                "share your key."
            )
        else:
            dm = (
                "⚠️ **Warning 1/3.** Your key is locked to a single computer and was "
                "just used on a different device. **Do not share your key** or it will "
                "be revoked."
            )

        # DM the buyer.
        user = self.get_user(link["user_id"])
        if user is None:
            try:
                user = await self.fetch_user(link["user_id"])
            except discord.HTTPException:
                user = None
        if user is not None:
            try:
                await user.send(dm)
            except discord.HTTPException:
                pass  # buyer has DMs closed

        # Log it in the warnings channel.
        channel = self._warnings_channel(link["guild_id"])
        if channel is not None:
            suffix = " — **auto-revoked** 🚫" if level >= 3 else ""
            await channel.send(
                f"⚠️ <@{link['user_id']}> (`{name}`) hit **Warning {level}/3**{suffix}",
                allowed_mentions=discord.AllowedMentions(users=False),
            )

    @tasks.loop(seconds=config.WARNING_POLL_SECONDS)
    async def warning_watcher(self) -> None:
        try:
            await self.check_warnings()
        except Exception:  # noqa: BLE001
            log.exception("warning_watcher pass failed")

    @warning_watcher.before_loop
    async def _before_warning_watcher(self) -> None:
        await self.wait_until_ready()


# --- Slash command ---------------------------------------------------------
@discord.app_commands.command(
    name="panel", description="Post the crypto purchase panel (owner only)."
)
async def panel_command(interaction: discord.Interaction) -> None:
    if config.OWNER_ID and interaction.user.id != config.OWNER_ID:
        await interaction.response.send_message(
            "Only the owner can post the panel.", ephemeral=True
        )
        return

    enabled = config.enabled_coins()
    if not enabled:
        await interaction.response.send_message(
            "No coins are configured. Set LTC_ADDRESS / SOL_ADDRESS / ETH_ADDRESS "
            "in your .env first.",
            ephemeral=True,
        )
        return

    coin_list = ", ".join(meta["name"] for meta in enabled.values())
    product_lines = "\n".join(
        f"**{p['name']}** — ${p['price_usd']:.2f}" for p in config.PRODUCTS
    )
    title = config.PRODUCT_NAME if len(config.PRODUCTS) == 1 else "Shop"
    embed = discord.Embed(
        title=f"🛒 {title}",
        description=(
            f"{product_lines}\n\n"
            "Pick a product + payment method below. A private ticket will open "
            "with payment details. Once your payment is confirmed on-chain, staff "
            f"are automatically pinged to hand over your key.\n\n**Accepted:** {coin_list}"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="Payments are detected automatically — send the exact amount shown.")
    await interaction.response.send_message(embed=embed, view=PurchasePanel())


@discord.app_commands.command(
    name="supportpanel", description="Post the support ticket panel (owner only)."
)
async def support_panel_command(interaction: discord.Interaction) -> None:
    if config.OWNER_ID and interaction.user.id != config.OWNER_ID:
        await interaction.response.send_message(
            "Only the owner can post the panel.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🎫 Support",
        description=(
            "Need help? Click a button below to open a **private support ticket** "
            "with our staff. Please only open a ticket if you genuinely need help."
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="One ticket per person — staff will respond as soon as they can.")
    await interaction.response.send_message(embed=embed, view=SupportPanel())


@discord.app_commands.command(
    name="linkkey",
    description="Link a license key to a buyer so they're warned if it's shared (staff only).",
)
@discord.app_commands.describe(
    code="The license code you gave the buyer",
    user="The buyer who received the key",
)
async def linkkey_command(
    interaction: discord.Interaction, code: str, user: discord.User
) -> None:
    if not is_staff_member(interaction.user):
        await interaction.response.send_message(
            "Only staff can link keys.", ephemeral=True
        )
        return
    try:
        jti, label = decode_key(code)
    except Exception:  # noqa: BLE001
        jti, label = "", ""
    if not jti:
        await interaction.response.send_message(
            "That doesn't look like a valid license code.", ephemeral=True
        )
        return
    db.link_key(jti, user.id, label, interaction.guild_id)
    await interaction.response.send_message(
        f"🔗 Linked key **{label or jti[:8]}** to {user.mention}. "
        "They'll be DM'd (and it'll post in the warnings channel) if it's shared.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions(users=False),
    )


def main() -> None:
    try:
        PaymentBot(privileged=True).run(config.DISCORD_TOKEN)
    except discord.PrivilegedIntentsRequired:
        log.warning(
            "Privileged intents are OFF in the Discord Developer Portal — running "
            "without them. Ticket transcripts (Message Content Intent) and invite "
            "tracking (Server Members Intent) stay disabled until you enable both "
            "under Developer Portal -> Bot -> Privileged Gateway Intents."
        )
        PaymentBot(privileged=False).run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
