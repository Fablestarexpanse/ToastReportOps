-- The actual photo bytes, saved to the aircraft's ID.
--
-- 0024 cached the planespotters URL. That is borrowed and can vanish — a photo
-- gets deleted, the CDN path rotates. So when a photo is found we now download
-- it into the content-addressed raw store (the same archive that already
-- replicates offsite) and keep the sha here. The picture becomes ours, tied to
-- the hull's hex, and survives the source removing it.
ALTER TABLE aircraft_photos ADD COLUMN image_sha TEXT;
