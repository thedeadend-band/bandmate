# Confirmed gigs, resources and rehearsals

Phase 5 adds local preparation tools to the existing Booking module. Open
**Confirmed gigs** in Booking, or **Setlist & gig preparation** on a confirmed
opportunity. No additional credentials, services or scheduled jobs are required.

## Setlists and private preparation

Booking editors/managers can select an existing same-band setlist and save private
gig notes. Linking does not change the setlist's name, owner, date, songs or export
behavior. Its existing owner/staff editing permissions still apply; a Booking
editor does not gain permission to edit another member's setlist.

Alternatively, explicitly confirm creation of a new band-visible empty setlist.
It uses the gig name/date and the initiating user as owner, then opens the existing
setlist editor to choose songs. The disclosure matters: setlists are visible to
ordinary band members, while gig notes remain accessible only through Booking.
Optimistic plan revisions and a band transaction prevent a duplicate click from
creating a second setlist. Choose a blank setlist and save to unlink it; this never
deletes the setlist. Deleting a setlist through its existing controls clears gig
and rehearsal references without removing preparation history.

New or changed preparation requires an active, unsuppressed confirmed opportunity.
Closing, suppressing or merging a gig preserves its preparation for reference and
blocks further preparation changes. Existing rehearsals can still be completed
or cancelled. A merge retains preparation on the archived source rather than
silently replacing the target's setlist; open the archived record to review it.
Opportunity name/date changes never silently update a setlist or published event.

The setlist player and linked local/Google Calendar event detail offer a route back
to active confirmed gig preparation. These links require current Booking access.
Ordinary band members and staff without a Booking role do not see private gig
details. Disabling Booking hides the links while leaving Calendar and Setlists usable.

## EPK and resource links

Managers maintain **EPK & resources**: named HTTP/HTTPS links for EPKs, stage plots,
technical riders, videos and other references, with private notes and revisions.
Links with embedded credentials or executable/non-web schemes are rejected.
BandMate stores links; it does not fetch, upload, publish, verify destination access,
or inspect the files. Keep secrets out of URLs and review the destination's sharing
settings before approving it for outreach.

Only active EPK resources explicitly marked **approved for external outreach**
are offered in the draft composer. Select one or enter a separate EPK URL. A
template using `{{epk_url}}` copies that exact URL into the editable draft. The
draft records resource identity/revision as provenance. Resource notes are never
copied. Other resource types remain reference links; they are not attachments.

Changing, unapproving or archiving a resource removes it from future selections.
Existing drafts and approved messages keep their copied content. Review/edit any
affected draft, or cancel an unstarted send approval, separately. The existing full
message review and manager-only send step remain required. Merely selecting an EPK
does not contact a provider or grant AI disclosure permission.

## Rehearsal workflow

Editors/managers plan a rehearsal with a date, practice focus and optional band
setlist. The gig's current setlist is offered initially; each rehearsal retains
its own selection even if the gig setlist changes later. Repeated creation with
the same action key returns the original record. Edits and status changes reject
stale revisions. A planned rehearsal can be edited, completed or cancelled; a
completed/cancelled rehearsal can be reopened only while its gig is eligible.

Planned rehearsals appear as **day reminders** in the existing Booking milestones
Calendar source, with a link to gig preparation. Completed/cancelled rehearsals,
closed/archived/suppressed gigs, and other bands' data are omitted. Calendar overlay
text does not include private focus notes. This is a local planning aid, without
time-of-day scheduling, recurring rehearsals, attendance tracking, notifications
or remote calendar writes. Use Calendar's existing explicit editor for timed events.

## Data and rollout

Migration `booking/0006_gig_preparation` adds `BookingGigPlan`, `BookingRehearsal`
and `BookingResource`. It does not rewrite existing player, ICS, song or setlist
rows. Ordinary application backups include the new tables; no new storage or
environment variables are needed. Existing record/usage exports do not export the
new preparation tables. A database backup is required to preserve these records.

The migration is reversible, but reversing it discards preparation, rehearsal and
resource rows. Setlists created by the workflow remain ordinary player setlists.
Before a real rollback, stop writes, take/verify a backup, review any new setlists
and use the existing pinned deployment procedure. No real rollback or production
migration was performed during development.

This implements the planned gig-to-setlist/resource/rehearsal portion of Phase 5.
Shared public caches, social adapters, hosted EPK assets, unattended application
submission and database/queue scaling are not included. They remain optional
future work with their own provenance, privacy and operational requirements.
