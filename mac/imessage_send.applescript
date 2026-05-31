-- imessage_send.applescript
-- Usage: osascript imessage_send.applescript "recipient" "message text"
--
-- recipient: an iMessage handle — phone in E.164 ("+12073854041") or iCloud
--            email ("tylumiere25@icloud.com"). The handle must be reachable
--            via iMessage (blue bubble) — green-bubble SMS recipients require
--            the SMS-relay feature on the sending Mac.
--
-- message:   plain text. Newlines OK.
on run argv
    if (count of argv) < 2 then
        error "usage: imessage_send.applescript <recipient> <message>"
    end if
    set recipientHandle to item 1 of argv
    set messageText to item 2 of argv

    tell application "Messages"
        set iMessageService to 1st service whose service type = iMessage
        set targetBuddy to buddy recipientHandle of iMessageService
        send messageText to targetBuddy
    end tell
    return "sent"
end run
