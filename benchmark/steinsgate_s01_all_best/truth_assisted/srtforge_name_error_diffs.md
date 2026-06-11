# Srtforge Name-Error-Excluded WER Audit

This audit is for `srtforge_fv_whisper_int8_float16` only. Rows marked `excluded_name_error` are the edit chunks removed from the Gemini-style name-correction estimate. Rows marked `counted_remaining_error` still contribute to the 5.26% WER.

## Per-Episode Summary

| Episode | Truth | Normal WER % | Name-error-excluded WER % | Ref words | Normal errors | Remaining errors | Excluded name errors |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S01E01 | original_ass | 8.95 | 7.96 | 2425 | 217 | 193 | 24 |
| S01E02 | assisted | 5.45 | 4.39 | 2732 | 149 | 120 | 29 |
| S01E03 | assisted | 6.73 | 5.80 | 3106 | 209 | 180 | 29 |
| S01E04 | assisted | 7.05 | 5.06 | 3022 | 213 | 153 | 60 |
| S01E05 | assisted | 5.65 | 4.98 | 3115 | 176 | 155 | 21 |
| S01E06 | assisted | 6.71 | 6.05 | 2891 | 194 | 175 | 19 |
| S01E07 | assisted | 8.02 | 6.80 | 2530 | 203 | 172 | 31 |
| S01E08 | assisted | 6.62 | 5.65 | 2763 | 183 | 156 | 27 |
| S01E09 | assisted | 7.03 | 5.37 | 2944 | 207 | 158 | 49 |
| S01E10 | assisted | 6.95 | 5.60 | 2805 | 195 | 157 | 38 |
| S01E11 | assisted | 7.16 | 6.10 | 2556 | 183 | 156 | 27 |
| S01E12 | assisted | 4.83 | 4.13 | 2855 | 138 | 118 | 20 |
| S01E13 | assisted | 7.90 | 5.45 | 2038 | 161 | 111 | 50 |
| S01E14 | assisted | 3.68 | 2.92 | 2607 | 96 | 76 | 20 |
| S01E15 | assisted | 6.13 | 5.31 | 2902 | 178 | 154 | 24 |
| S01E16 | assisted | 5.49 | 4.73 | 2730 | 150 | 129 | 21 |
| S01E17 | assisted | 7.26 | 4.52 | 2412 | 175 | 109 | 66 |
| S01E18 | assisted | 4.28 | 3.59 | 3064 | 131 | 110 | 21 |
| S01E19 | assisted | 9.18 | 6.72 | 1874 | 172 | 126 | 46 |
| S01E20 | assisted | 8.66 | 6.74 | 1928 | 167 | 130 | 37 |
| S01E21 | assisted | 6.16 | 4.46 | 2532 | 156 | 113 | 43 |
| S01E22 | assisted | 4.58 | 3.80 | 2314 | 106 | 88 | 18 |
| S01E23 | assisted | 5.94 | 4.88 | 2744 | 163 | 134 | 29 |
| S01E24 | assisted | 7.42 | 6.51 | 2427 | 180 | 158 | 22 |

## Example Excluded Name Chunks

### S01E01

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | okarin | ocarine | heedless of what really underwrites natural law <<okarin>> yeah yeah infiltration commencing el psy congroo |
| substitute | oopa | oompa | huh i don't believe it a metal <<oopa>> and that's good ahh you bet it |
| substitute | okarin hououin kyoma | ocarine hoi inkyoma | would know better than i thank you <<okarin hououin kyoma>> whatever you're the best my name is |
| substitute | titor | teeter | kerr black holes two words sir john <<titor>> oh yes i submit to all and |
| substitute | titor's | teeter's | oh please far from it my friend <<titor's>> so called theories are borderline schizophrenic well |
| substitute | makise | makisei | the nut house hold on a second <<makise>> kurisu the published makise kurisu okay that's |

### S01E02

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | nakabachi's | nakaboxi's | more than three hours ago during dr <<nakabachi's>> lecture ix nay on the azy cray |
| substitute | hououin kyoma | thank you | are sick far from it i am <<hououin kyoma>> first off thanks for coming today i |
| substitute | hououin kyoma | in kiyoma | by entrenched naysayers now would we agreed <<hououin kyoma>> that infuriating safe to say string theory |
| substitute | okarin's | ocarine's | the great ruka is victorious oh hey <<okarin's>> here too too roo huh oh okabe |
| substitute | rukako | rukiko | has taken possession of my right hand <<rukako>> banish it before i'm forced to do |
| substitute | urushibara | urishibara | while you're here indeed it is insatiable <<urushibara>> ruka delicate as an orchid fair as |

### S01E03

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | makise | makisei | <<makise>> kurisu hey sorry i tried knocking but |
| substitute | makise | makisei | too much time in the states i'm <<makise>> kurisu might as well do it official |
| substitute | hououin | hoeing | the zombie how would you like it <<hououin>> carcinoma kyoma watch your tongue banana mouth |
| substitute | chrrrristina | christina | member double o four also known as <<chrrrristina>> no i am not when you're on |
| substitute | makise kurisu | maki sekurisu | guest a pleasure to meet you i'm <<makise kurisu>> i hope my being here didn't startle |
| substitute | shiina | shina | my being here didn't startle you i'm <<shiina>> mayuri are you a member of the |

### S01E04

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | ibn | ibm | code he was say something like the <<ibn>> 5100 was built before pcs were using |
| substitute | ibn's | ibm's | were using basic so it can read <<ibn's>> proprietary programming language the ibn 5100's singular |
| substitute | ibn | ibm | can read ibn's proprietary programming language the <<ibn>> 5100's singular abilities are needed to halt |
| substitute | sern's | cern's | 5100's singular abilities are needed to halt <<sern's>> ambitions i would very much like to |
| substitute | slang wassabi suzuha | sled wasabi sisuha | from what pit do you mine your <<slang wassabi suzuha>> cut it out wait a sec since |
| substitute | okarin | ocarina | gold where you off to oh laundry <<okarin>> and daru have been working late so |

### S01E05

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | okabe wasssup | what's up | weakness what we weren't conspicuous enough already <<okabe wasssup>> what's in the box heh dying to |
| substitute | ibn | ibm | paced denouement let me guess it's an <<ibn>> 5100 what how did she pluck the |
| substitute | yanabayashi | yanabayashi's | it down fate guided my footsteps to <<yanabayashi>> shrine where it languished amid the dusty |
| substitute | makise kurisu | cursed sue | your footsteps if we don't hurry up <<makise kurisu>> uh uh huh who is this person |
| substitute | daru | dara | do have a right dastardly face huh <<daru>> your t shirt is so big it's |
| delete | it's kurisu | <empty> | now easy does it thanks captain obvious <<it's kurisu>> oh hey mayuri sorry to just barge |

### S01E06

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | okarin's treat | ocarine street | do this again tomorrow all of us <<okarin's treat>> no no no don't put me on |
| substitute | sern's | cern's | my minions if we intend to nip <<sern's>> odious design in the proverbial bud perfecting |
| substitute | sern is | and it's | let me put it this way then <<sern is>> very very bad okay but what do |
| substitute | kyouma's | kiyoma's | from thin air we shall call it <<kyouma's>> nostalgia drive i propose referring to it |
| substitute | kyouma's | kiyoma's | of hands who here is good with <<kyouma's>> nostalgia drive come on neither of you |
| substitute | 'sides kyoma | sides kiyoma | makes me think of a charity yeah <<'sides kyoma>> nice try in the interest of keeping |

### S01E07

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | phone | phonewave | check it ladies the upgrade is complete <<phone>> wave mark ii version one 03 is |
| substitute | prawn | braun | to anyone i sure do hope mr <<prawn>> doesn't get mad at us again part |
| substitute | okarin | ocarine | how are you going to prove it <<okarin>> thank you so much for asking by |
| substitute | okarin | corrine | picking the winning numbers in the lottery <<okarin>> i'm disappointed in you dude sick i'm |
| substitute | oopa | a | we could give everyone on earth an <<oopa>> cushion seriously man way to be a |
| insert | <empty> | maki | don't you have a lotto to fix <<<gap>>> makise's right we should figure that out |

### S01E08

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | titor | teeter | us sending that d mail according to <<titor>> i might have an unprecedented skill the |
| substitute | faris | ferris | of the cards in his match with <<faris>> so he should have won hey mayuri |
| substitute | faris | ferris | question but when i went up against <<faris>> the other day did i sorry what |
| substitute | experim moeka | experiments moaka | we're going to have to conduct more <<experim moeka>> shut up can i you are a |
| substitute | nae | nye | least little thing to her come on <<nae>> let's go inside before this loser freaks |
| substitute | it's mayuri too too roo | do do do do do | than he already has good morning yay <<it's mayuri too too roo>> brought you something wow my favorite and |

### S01E09

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | faris in | ferris and | we didn't exactly improve my chances against <<faris in>> the rai net tourney now now we |
| substitute | kyoma | kyu | seriously i want you to die oh <<kyoma>> why keep secrets from each other my |
| substitute | faris | ferris | nice work out there today yeah thanks <<faris>> you're probably on your way to see |
| substitute | kyoma | kiyoma | you're probably on your way to see <<kyoma>> now huh mhmm he wants me to |
| substitute | okarin's | ocarines | time they use it it feels like <<okarin's>> further away this round table discussion will |
| substitute | mayushii | mayushi | please go on okay point of order <<mayushii>> said she wasn't cosplaying today but look |

### S01E10

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | okarin | ocarine | vanished from the streets of akihabara please <<okarin>> wait for us the hell dude i'm |
| substitute | faris | ferris | existed then how did we first meet <<faris>> you can mock my random bits of |
| substitute | faris | ferris | want but we wouldn't be friends with <<faris>> right now if it weren't for the |
| substitute | faris | ferris | and mayuri filled in for me against <<faris>> got both of your asses kicked in |
| substitute | kurisu | curusu | beach some weekend you got to come <<kurisu>> school swimsuits for the win i dunno |
| substitute | ruka | ruk | as i pointed out the other day <<ruka>> is shall we say of the masculine |

### S01E11

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | braun | brawn | you your poor head 'twas brains against <<braun>> i can't believe he actually hit me |
| substitute | sern's | certain | down to 36 bytes you do remember <<sern's>> subjects turning to pudding don't you nobody |
| substitute | okarin | ocarine | how we're having this conversation right now <<okarin>> yeah okay so pretend that my brain |
| substitute | moeka | huh | uh for me silly ooh look it's <<moeka>> hey moeka shining finger you look even |
| substitute | moeka | do | me silly ooh look it's moeka hey <<moeka>> shining finger you look even more serious |
| substitute | moeka's | moika's | don't just blurt it out awww but <<moeka's>> a lab member she's double o five |

### S01E12

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | okarin | huh | million years from now so it's okay <<okarin>> kurisu's mostly finished setting everything up and |
| substitute | huh earth to okarin | thank you thank you | er though my cosplay is almost done <<huh earth to okarin>> hey hold this a second for me |
| substitute | mayushii | mayushi | traipse around in public wearing finished yay <<mayushii>> takes home the gold i beat you |
| substitute | hououin | ween | know what you think about it not <<hououin>> daru yo yo am i to understand |
| substitute | hashida's | hashita | do we know what kind of pizza <<hashida's>> ordered no idea well it's not a |
| substitute | faris | ferris | it okay if we invite ruka and <<faris>> to the party 'cause we kind of |

### S01E13

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | makise | makisei | sern hold on you can't just storm <<makise>> kurisu okabe rintaro hashida itaru you'll be |
| substitute | mayuri | yuri | itaru you'll be coming with us and <<mayuri>> i'm sorry the little one isn't needed |
| substitute | okarin | karin | i was about to lose her mayuri <<okarin>> i'm not letting them take you you're |
| delete | huh i've got to get her into cosplay one of these days i give you permission to hug my oopa cushion is your head all empty or what | <empty> | guys you know what looks pretty cute <<huh i've got to get her into cosplay one of these days i give you permission to hug my oopa cushion is your head all empty or what>> what's with you did you doze off |
| substitute | mayushii | mayushi | cell phone too too roo this is <<mayushii>> actually it's my phone so leave me |
| substitute | suzuha | suzaha | are white honey after this mayuri brings <<suzuha>> to the lab and then do i |

### S01E14

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | mayuri | yuri | blur now but however hard i tried <<mayuri>> would always die right in front of |
| substitute | shiina | sheena | extra good care not again please mayuri <<shiina>> mayuri is confirmed dead hoe oh een |
| substitute | ibn | ibm | what our mission is to retrieve the <<ibn>> 5100 haven't you psychos already made off |
| substitute | ibn's | ibm's | the lab in the first place the <<ibn's>> not even frigging there i answer me |
| substitute | fb | b | overlords at sern been on to us <<fb>> is the only authority i'll ever answer |
| substitute | fb | b | and that's what like a splinter cell <<fb>> is my my everything where am i |

### S01E15

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | teeter thank you thank | you and me i post as john <<<gap>>> titor by the year 2036 sern has |
| substitute | titor | you | you and me i post as john <<titor>> by the year 2036 sern has taken |
| substitute | sern's | cern's | much as i wish they weren't but <<sern's>> a research organization it doesn't have a |
| substitute | titor | teeter | or anything like that i tried asking <<titor>> about it online a bunch of times |
| substitute | hououin kyoma | ki oma | in plain sight attributing them all to <<hououin kyoma>> yeah we know that once sern builds |
| substitute | sern's | cern's | did more than anyone else to bring <<sern's>> machine to fruition the mother of time |

### S01E16

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | okarin | ocarine | figure wait that hat daru aw man <<okarin>> what's gotten into you sneaking off to |
| substitute | suzuha's | sousa's | sneaking off to make a copy of <<suzuha's>> pin seriously how bad's she going to |
| substitute | okabe | okube | bad ass so i guess you're right <<okabe>> want to hop in and lend a |
| substitute | suzuha | susa | got i promise you i'll do it <<suzuha>> i'll keep the world exactly the way |
| substitute | daru nah | dari uh | be pretty nice what do you think <<daru nah>> i'd aim a bit earlier if you |
| substitute | itaru | taru | barrel and taru were the same taru <<itaru>> there ya go that's your reason word |

### S01E17

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | suzuha | susa | i think telling myself not to follow <<suzuha>> got us close enough to the one |
| substitute | suzuha | suzaha | what have we got we know that <<suzuha>> feels we can spare mayuri by shifting |
| substitute | sern's | cern's | do that we'd need to hack into <<sern's>> database and delete every shred of intel |
| substitute | ibn | ibm | the laboratory which means we'd need the <<ibn>> that's the thing it'd been in storage |
| substitute | moeka's | moek's | in storage at the yanabayashi shrine before <<moeka's>> d mail now though needle in a |
| substitute | kiryuu's | kiryu's | started all of this the lottery one <<kiryuu's>> ruka's faris' the one about suzuha apparently |

### S01E18

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | okarin | corrine | a bad joke out of the situation <<okarin>> i am so mad at you right |
| substitute | ruka send | rukas into | to undo is the one we let <<ruka send>> to his mother's pager while she was |
| substitute | urushibara | arushibara | while she was pregnant with him so <<urushibara>> really is a boy that's rather hard |
| substitute | hashida ooooh | hashira oh | next you'll tell me she looked like <<hashida ooooh>> no heh look wise ruka's no different |
| substitute | oooh kyouma's | ooh kiyoma's | taunt me i am not your assistant <<oooh kyouma's>> going out on a date that's interesting |
| substitute | okarin there | kareen there's | having this awkward conversation to begin with <<okarin there>> some special reason you were waiting to |

### S01E19

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | comima | ma | and cook noodles i had fun at <<comima>> this you're going to go back tomorrow |
| substitute | kurisu | curzu | so ooh you should come with me <<kurisu>> uh i dunno big crowds really aren't |
| substitute | okarin | ocarine | so it shouldn't be too bad hey <<okarin>> want to come with you roof now |
| substitute | mayuri's | uri's | way i can be sure how long <<mayuri's>> got on this world line and once |
| substitute | kiryuu moeka | kira yumoaka | next d mail it was from this <<kiryuu moeka>> girl right indeed and i know what |
| substitute | comima | kamima | for her i'll go with mayuri to <<comima>> what maybe we'll get lucky and she |

### S01E20

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | mayuri | uri | too long and i'll keep fighting until <<mayuri>> is safe the coin lockers at the |
| substitute | ibn | ibm | so maybe you can only get the <<ibn>> by going back to the first world |
| substitute | sern | cern's | because this fb person wants it in <<sern>> custody right ask yourself what's it doing |
| substitute | fb what're | what are | stake out the lockers and we'll find <<fb what're>> you doing here if she's coming i |
| substitute | moeka | moika | fine honey why're you waiting there with <<moeka>> have you lost your damn mind you |
| substitute | okarin | ocarine | what the hell that's just great that <<okarin>> oh uh yeah what's the matter i'm |

### S01E21

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | okabe | bay | it isn't necessary it can't be where's <<okabe>> no idea been too busy to keep |
| substitute | mayushii | mayushi | on him break a leg out there <<mayushii>> same to you soldier peace out so |
| substitute | comima yup | kamima yep | take this is the last day of <<comima yup>> you should totally come with me there's |
| substitute | okarin | ocarine | it'd wouldn't be my thing where did <<okarin>> run off to your guess is as |
| substitute | comima | commima | uncle heat stroke you going back to <<comima>> i assume yup wouldn't miss it yes |
| substitute | suzuha's | susaha's | originally it occurred on the thirteenth undoing <<suzuha's>> text moved it to the fourteenth then |

### S01E22

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | comima | kamima | he didn't see her at all at <<comima>> yeah i saw her afterward ah hey |
| substitute | okabe | ogabe | yeah i saw her afterward ah hey <<okabe>> what's up never mind then hey it's |
| substitute | okabe | okapai | on the original line and i end <<okabe>> wait you won't die i'm not going |
| substitute | for mayuri | from ayuri | i can't throw you away not even <<for mayuri>> what was the point of this repetitious |
| substitute | kurisu's | kurosu's | even but we've shared our problems too <<kurisu's>> the one i relied on when things |
| substitute | kurisu | kurosu | of our minds are connected forming the <<kurisu>> you know that would be wonderful wouldn't |

### S01E23

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | daru | taro | me it transforms it's a time machine <<daru>> sorry you're familiar with it that's good |
| substitute | makise kurisu | maki sekurisu | been entrusted with an important mission rescue <<makise kurisu>> and shift things to a world line |
| substitute | makise | makisei | said but this friend of yours this <<makise>> kurisu sounds to me like she's worth |
| substitute | kurisu's | curacao's | at the radio building it might throw <<kurisu's>> movements out of whack we're about to |
| substitute | okarin | corrine | so you might want to prepare yourself <<okarin>> what are these they come with the |
| substitute | makise | makisei | you do what you can to save <<makise>> wish me luck july 28th the day |

### S01E24

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | rintaro | rintoro | the past as you understood it okabe <<rintaro>> sees makise lying dead on the ground |
| substitute | hououin | in | yeah you are under the wing of <<hououin>> kyoma whose name among those in the |
| substitute | makise's | makisei's | if the me from july 28th discovers <<makise's>> unconscious form in said pool of gore |
| substitute | okarin | ovarine | or terrified i'm going with terrified hey <<okarin>> um did kurisu like sit in this |
| substitute | kurisu | kursu | going with terrified hey okarin um did <<kurisu>> like sit in this chair over here |
| substitute | makise kurisu | maki sekurisu | having trust me may a superstar like <<makise kurisu>> wouldn't slum it in a cave like |


## Example Remaining Counted Errors

### S01E01

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | all just | scientists often make very poor poets we're <<<gap>>> a ship of fools chasing phantoms heedless |
| substitute | psy congroo | sai kongru | law okarin yeah yeah infiltration commencing el <<psy congroo>> over and out place is a mortuary |
| delete | huh | <empty> | this why did we come here again <<huh>> you're here because i permit you to |
| substitute | ooo | ooh | here because i permit you to be <<ooo>> a time machine neat the good doctor |
| delete | hmph | <empty> | where our theories intersect if at all <<hmph>> huh the roof eh what the hell |
| delete | eh | <empty> | if at all hmph huh the roof <<eh>> what the hell is anything not an |

### S01E02

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| delete | you're | <empty> | what are you doing impossible how you're <<you're>> alive are you a zombie i'm about |
| substitute | ix nay | it's snay | three hours ago during dr nakabachi's lecture <<ix nay>> on the azy cray dude we've been |
| substitute | azy cray | az crate | dr nakabachi's lecture ix nay on the <<azy cray>> dude we've been over this the doctor's |
| substitute | ma'am uh | ma 'am | excuse me they're ready for you now <<ma'am uh>> coming you can't just saunter off no |
| insert | <empty> | sort | now ma'am uh coming you can't just <<<gap>>> saunter off no you don't you are |
| substitute | saunter | her | now ma'am uh coming you can't just <<saunter>> off no you don't you are sick |

### S01E03

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | how | how'd | tried knocking but no one came but <<how>> did you ferret us out i asked |
| delete | did | <empty> | knocking but no one came but how <<did>> you ferret us out i asked hashida |
| substitute | hah | aha | deal with a three dimensional woman well <<hah>> the battle for daru's soul is not |
| insert | <empty> | on | for daru's soul is not over i'm <<<gap>>> onto you mata hari could you please |
| substitute | onto | to | for daru's soul is not over i'm <<onto>> you mata hari could you please tell |
| substitute | mata | matahari | soul is not over i'm onto you <<mata>> hari could you please tell him i'm |

### S01E04

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | you | is this some kind of proprietary code <<<gap>>> eff this i officially have no idea |
| substitute | eff | f | is this some kind of proprietary code <<eff>> this i officially have no idea what |
| delete | hhhh | <empty> | a good time to take a break <<hhhh>> who did i think i was unreadable |
| substitute | you've | you | think i was unreadable code is unreadable <<you've>> fought nobly sir take a breather take |
| substitute | freaking | fricking | nobly sir take a breather take a <<freaking>> nap more like i got some more |
| substitute | may | mai | some more noodles if you want thanks <<may>> i'll take it lying down proprietary code |

### S01E05

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| delete | a | <empty> | i'm calling <<a>> it a day help me lock up |
| delete | breaks | <empty> | eyes were begging me to take those <<breaks>> yeah because you looked like you were |
| substitute | looked | look | to take those breaks yeah because you <<looked>> like you were about to up and |
| substitute | you | you're | those breaks yeah because you looked like <<you>> were about to up and die every |
| delete | were | <empty> | breaks yeah because you looked like you <<were>> about to up and die every couple |
| substitute | na | naive | die every couple of feet don't be <<na>> ve i was merely feigning weakness what |

### S01E06

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | first | street below in hopes of catching a <<<gap>>> firsthand glimpse and as no country has |
| substitute | firsthand | hand | street below in hopes of catching a <<firsthand>> glimpse and as no country has yet |
| insert | <empty> | you thank you thank you | viewers at home where you've traveled from <<<gap>>> wowza are you kidding it's awesome this |
| substitute | well | will | the hype doesn't do them justice yes <<well>> you really ought to try it with |
| substitute | egg | eggs | really ought to try it with raw <<egg>> sometime that is of course once you've |
| insert | <empty> | un | is of course once you've mastered this <<<gap>>> unwarrior like phobia of eating out alone |

### S01E07

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | curs d | curse it | <<curs d>> be these hands what granted you life |
| substitute | sulphurous | sulfurous | what granted you life now honor our <<sulphurous>> contract writ in blood let the ebon |
| substitute | writ | written | you life now honor our sulphurous contract <<writ>> in blood let the ebon flames burn |
| delete | in | <empty> | life now honor our sulphurous contract writ <<in>> blood let the ebon flames burn black |
| substitute | o | oh | ebon flames burn black fulfill my wish <<o>> beast ahhhhh how'm i going to heat |
| substitute | ahhhhh how'm | how am | burn black fulfill my wish o beast <<ahhhhh how'm>> i going to heat up my chicken |

### S01E08

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | you | <<<gap>>> me humanity's savior nonsense i have chosen |
| substitute | vie | fight | the path of the mad scientist i <<vie>> for chaos and destruction dude you're freaking |
| substitute | wyrd | weird | me out we shall be recommencing operation <<wyrd>> without further delay okay but you know |
| insert | <empty> | my | of earlier today differ so significantly from <<<gap>>> mine oh yeah you mean that whole |
| substitute | mine | own | of earlier today differ so significantly from <<mine>> oh yeah you mean that whole lottery |
| insert | <empty> | sending | are you the only one who remembers <<<gap>>> us sending that d mail according to |

### S01E09

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | all | <<<gap>>> alright we've established beyond a reasonable doubt |
| substitute | alright | right | <<alright>> we've established beyond a reasonable doubt that |
| substitute | rai net tourney now | rynette turney oh no | improve my chances against faris in the <<rai net tourney now>> now we can't work miracles sure in |
| delete | now | <empty> | faris in the rai net tourney now <<now>> we can't work miracles sure in ruka's |
| insert | <empty> | two | will be fully operational with one or <<<gap>>> time machine mind your business that's exactly |
| substitute | awwww | aw | milk sugar or flavored syrup no thanks <<awwww>> having trouble choosing meow meow nuh uh |

### S01E10

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | what | of akihabara please okarin wait for us <<<gap>>> the hell dude i'm a hacker not |
| insert | <empty> | hacker why'd you run off where | hell dude i'm a hacker not a <<<gap>>> hiker where'd toranoana and animate go huh |
| substitute | hiker where'd toranoana | toru no ana | hell dude i'm a hacker not a <<hiker where'd toranoana>> and animate go huh where're mandarake and |
| insert | <empty> | where | hiker where'd toranoana and animate go huh <<<gap>>> where're mandarake and gamers now you're just |
| substitute | where're | are | hiker where'd toranoana and animate go huh <<where're>> mandarake and gamers now you're just being |
| substitute | now | no | animate go huh where're mandarake and gamers <<now>> you're just being silly you know we |

### S01E11

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | those | doesn't work i'll bust out the guns <<<gap>>> those'll do double o eight is on |
| substitute | those'll | will | doesn't work i'll bust out the guns <<those'll>> do double o eight is on it |
| substitute | double | 008 | i'll bust out the guns those'll do <<double>> o eight is on it you're starting |
| delete | o eight | <empty> | bust out the guns those'll do double <<o eight>> is on it you're starting with charm |
| insert | <empty> | the | eight is on it you're starting with <<<gap>>> charm can't you tell fire at will |
| insert | <empty> | right | is on it you're starting with charm <<<gap>>> can't you tell fire at will target |

### S01E12

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | but | though | were brought here by a time machine <<but>> you probably won't remember that yet i |
| substitute | world | worldlines | after you through so many mixed up <<world>> lines that i don't even remember how |
| delete | lines | <empty> | you through so many mixed up world <<lines>> that i don't even remember how far |
| insert | <empty> | of | it was i lost count please i <<<gap>>> 'course the you i followed here is |
| substitute | 'course | course | it was i lost count please i <<'course>> the you i followed here is just |
| substitute | i | i'm | of a bunch of versions too but <<i>> sure i'm the original version of me |

### S01E13

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | my | itaru you'll be coming with us and <<<gap>>> mayuri i'm sorry the little one isn't |
| insert | <empty> | to you | kill you if you don't i swear <<<gap>>> on my life you will pay the |
| insert | <empty> | why thank you thank you thank | goddammit do as i say okabe please <<<gap>>> work mayuri's grandma died when we were |
| substitute | work | you | goddammit do as i say okabe please <<work>> mayuri's grandma died when we were young |
| insert | <empty> | and | there her grandma's pocket watch in hand <<<gap>>> she'd gaze patiently into the sky as |
| delete | as if | <empty> | gaze patiently into the sky as if <<as if>> the woman was coming back for her |

### S01E14

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | my | blur now but however hard i tried <<<gap>>> mayuri would always die right in front |
| substitute | hold | holdup | did could change its mind what's the <<hold>> up this is ridiculous train lines have |
| delete | up | <empty> | could change its mind what's the hold <<up>> this is ridiculous train lines have shut |
| substitute | that's | it's | an alley or something come on man <<that's>> not really an option sorry oh no |
| insert | <empty> | of you | not fair i've taken extra good care <<<gap>>> not again please mayuri shiina mayuri is |
| substitute | not again | and everything | not fair i've taken extra good care <<not again>> please mayuri shiina mayuri is confirmed dead |

### S01E15

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | squads' | squad's | day and night living in the hit <<squads'>> crosshairs i realized they couldn't be defeated |
| insert | <empty> | kami | the nom de web chestnut rice and <<<gap>>> kamehame ha hey she laid her cards |
| substitute | kamehame | hami | the nom de web chestnut rice and <<kamehame>> ha hey she laid her cards out |
| insert | <empty> | ho in | in plain sight attributing them all to <<<gap>>> hououin kyoma yeah we know that once |
| insert | <empty> | who | builds a time machine they're the ones <<<gap>>> who're calling all the shots with that |
| substitute | who're | are | builds a time machine they're the ones <<who're>> calling all the shots with that kind |

### S01E16

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | pin | pen | and asked me to make him a <<pin>> and the sketch he showed me looked |
| substitute | pin | pen | sketch he showed me looked like the <<pin>> you came around with sort of like |
| substitute | ya | you | but he was round i can tell <<ya>> that much round like a barrel didja |
| insert | <empty> | did | ya that much round like a barrel <<<gap>>> didja know taru is another word for |
| substitute | didja | you | ya that much round like a barrel <<didja>> know taru is another word for barrel |
| substitute | rai | reina | i picked that up from watching the <<rai>> net anime there was a barrel monster |

### S01E17

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | a | braun's house nothing not so much as <<<gap>>> car backfiring up to that point the |
| substitute | thirteenth | 13th | car backfiring up to that point the <<thirteenth>> had always been d day i think |
| delete | percent | <empty> | got us close enough to the one <<percent>> maybe hopefully just please please let this |
| substitute | your | you're | you a while ago don't tell me <<your>> broken so not fair i've taken extra |
| insert | <empty> | thank you thank you | extra special care of you and everything <<<gap>>> okay you've got my attention why are |
| delete | to | <empty> | you've been time leaping like a madman <<to>> trying to stop it haven't you must've |

### S01E18

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | thank you thank you | yourself to give up being a girl <<<gap>>> what's the damage you're sitting there like |
| insert | <empty> | of course | being told i look like a busboy <<<gap>>> 'course could have gone the other way |
| substitute | 'course | you | being told i look like a busboy <<'course>> could have gone the other way with |
| insert | <empty> | a | the other way with it called me <<<gap>>> noodle well hey at least you still |
| substitute | wring | ring | least you still have the gumption to <<wring>> a bad joke out of the situation |
| insert | <empty> | oh | a bad joke out of the situation <<<gap>>> okarin i am so mad at you |

### S01E19

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | thank you thank you thank you thank you | <<<gap>>> hurry up and cook noodles i had |
| insert | <empty> | kami | and cook noodles i had fun at <<<gap>>> comima this you're going to go back |
| insert | <empty> | afternoon | noodles i had fun at comima this <<<gap>>> you're going to go back tomorrow aren't |
| insert | <empty> | don't | should come with me kurisu uh i <<<gap>>> dunno big crowds really aren't my thing |
| substitute | dunno | know | should come with me kurisu uh i <<dunno>> big crowds really aren't my thing but |
| insert | <empty> | my | way i can be sure how long <<<gap>>> mayuri's got on this world line and |

### S01E20

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | i've | i | i don't leave things to fate anymore <<i've>> fought against it for far too long |
| delete | for | <empty> | to fate anymore i've fought against it <<for>> far too long and i'll keep fighting |
| insert | <empty> | my | too long and i'll keep fighting until <<<gap>>> mayuri is safe the coin lockers at |
| substitute | dai | die | is safe the coin lockers at the <<dai>> building the computer is inside one of |
| substitute | that | the | obvious isn't it i'm going to pry <<that>> coin locker open you can give it |
| substitute | we've | we | things in order no cutting corners here <<we've>> finally found it and it does us |

### S01E21

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | thank you thank you | stop it isn't necessary it can't be <<<gap>>> where's okabe no idea been too busy |
| insert | <empty> | zoka | it isn't necessary it can't be where's <<<gap>>> okabe no idea been too busy to |
| insert | <empty> | it | you soldier peace out so i take <<<gap>>> this is the last day of comima |
| substitute | it'd | it | to scope out uh i'm pretty sure <<it'd>> wouldn't be my thing where did okarin |
| insert | <empty> | huh | pretty sure it'd wouldn't be my thing <<<gap>>> where did okarin run off to your |
| substitute | heat stroke | heatstroke you're | they don't want a visit from uncle <<heat stroke>> you going back to comima i assume |

### S01E22

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| insert | <empty> | thank you thank you | d mail you'll be you'll be murdered <<<gap>>> ah there you are don't stay up |
| substitute | ah | oh | d mail you'll be you'll be murdered <<ah>> there you are don't stay up here |
| substitute | heat stroke | heatstroke seth's | long you draw the ire of uncle <<heat stroke>> says the guy who's always sprawled out |
| delete | says | <empty> | draw the ire of uncle heat stroke <<says>> the guy who's always sprawled out on |
| insert | <empty> | did | a cat it's cloudy today anyway hey <<<gap>>> d'you find mayuri daru said he didn't |
| substitute | d'you | you | a cat it's cloudy today anyway hey <<d'you>> find mayuri daru said he didn't see |

### S01E23

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | three | iii | seriously world war <<three>> let me put it into perspective for |
| substitute | world | worldline | i refuse okarin i've been jumping from <<world>> line to world line for weeks i've |
| delete | line | <empty> | refuse okarin i've been jumping from world <<line>> to world line for weeks i've run |
| substitute | world | worldline | i've been jumping from world line to <<world>> line for weeks i've run interference so |
| delete | line | <empty> | been jumping from world line to world <<line>> for weeks i've run interference so many |
| substitute | world | worldline | was able to land back onto this <<world>> line was by allowing kurisu to die |

### S01E24

| Op | Reference | Hypothesis | Context |
| --- | --- | --- | --- |
| substitute | skuld | skull | and to further please our whimsy operation <<skuld>> will be the name we assign this |
| substitute | detectibly | detectably | you must to save the future without <<detectibly>> altering the past as you understood it |
| insert | <empty> | intents | own blood keep the past for all <<<gap>>> intensive purposes where it is deceive yourself |
| substitute | intensive | and | own blood keep the past for all <<intensive>> purposes where it is deceive yourself in |
| insert | <empty> | all | you keep calling me that chop chop <<<gap>>> alright folks this is our line in |
| substitute | alright | right | you keep calling me that chop chop <<alright>> folks this is our line in the |
