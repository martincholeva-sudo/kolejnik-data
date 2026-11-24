"""Hand-written synthetic CIS/NeTEx fixtures; these are not real timetables."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from national.rail import RailMessages
from national.ostrava import OstravaNetex
from national.bundle import compose
from national.common import blank, finalize, add_service

DAY = '2026-10-26'

def ident(path='PATH-TEST', train='TRAIN-TEST'):
    def one(kind, core):
        return f'<PlannedTransportIdentifiers><ObjectType>{kind}</ObjectType><Company>TEST</Company><Core>{core}</Core><Variant>01</Variant><TimetableYear>2026</TimetableYear></PlannedTransportIdentifiers>'
    return '<Identifiers>' + one('PA', path) + one('TR', train) + '</Identifiers>'

def cal(bits='111'):
    return '<PlannedCalendar><BitmapDays>' + bits + '</BitmapDays><ValidityPeriod><StartDateTime>2026-10-26T00:00:00</StartDateTime><EndDateTime>2026-10-28T00:00:00</EndDateTime></ValidityPeriod></PlannedCalendar>'

def loc(code, name, time, operator='TEST-NEW-CARRIER', public=True, activity='0001', offset=0):
    return f'''<CZPTTLocation><Location><CountryCodeISO>CZ</CountryCodeISO><LocationPrimaryCode>{code}</LocationPrimaryCode><PrimaryLocationName>{name}</PrimaryLocationName></Location><TimingAtLocation><Timing TimingQualifierCode="ALA"><Time>{time}</Time><Offset>{offset}</Offset></Timing><Timing TimingQualifierCode="ALD"><Time>{time}</Time><Offset>{offset}</Offset></Timing></TimingAtLocation><ResponsibleRU>{operator}</ResponsibleRU><TrainType>{'1' if public else '2'}</TrainType><OperationalTrainNumber>TEST100</OperationalTrainNumber><TrainActivity><TrainActivityType>{activity}</TrainActivityType></TrainActivity></CZPTTLocation>'''

def train(path='PATH-TEST', operator='TEST-NEW-CARRIER', bits='111', stamp='2026-10-01T12:00:00Z', points=None, train_id=None):
    points = points if points is not None else [loc('A','TEST Praha stanice','06:00:00',operator), loc('B','TEST mezistanice','07:00:00',operator),loc('C','TEST Ostrava stanice','09:00:00',operator)]
    return ET.fromstring('<CZPTTCISMessage xmlns="urn:test:cis">' + ident(path, train_id or 'TRAIN-'+path) + '<CZPTTCreation>' + stamp + '</CZPTTCreation><CZPTTInformation>' + ''.join(points) + cal(bits) + '</CZPTTInformation></CZPTTCISMessage>')

def cancellation(path='PATH-TEST', bits='010', stamp='2026-10-02T12:00:00Z', section=None):
    section_xml = '' if section is None else '<CZDeactivatedSection>' + ''.join('<'+tag+'><CountryCodeISO>CZ</CountryCodeISO><LocationPrimaryCode>'+code+'</LocationPrimaryCode></'+tag+'>' for tag,code in zip(['StartLocation','EndLocation'],section)) + '</CZDeactivatedSection>'
    calendar = cal(bits) if section is None else '<PlannedCalendar><StartDate>2026-10-27</StartDate></PlannedCalendar>'
    return ET.fromstring('<CZCanceledPTTMessage>' + ident(path,'TRAIN-'+path) + '<CZPTTCancelation>' + stamp + '</CZPTTCancelation>' + calendar + section_xml + '</CZCanceledPTTMessage>')

def ostrava(mode='tram', ico='61974757', pickup='true', bits='111', code='TEST1', departure='09:15:00', arrival='09:30:00'):
    return ET.fromstring(f'''<PublicationDelivery xmlns="http://www.netex.org.uk/netex"><dataObjects><CompositeFrame><frames>
<ResourceFrame><organisations><Operator id="OP"><PublicCode>{ico}</PublicCode><LegalName>{'Dopravní podnik Ostrava a.s.' if ico=='61974757' else 'TEST Jiný podnik'}</LegalName></Operator></organisations></ResourceFrame>
<SiteFrame><stopPlaces><StopPlace id="PLACE-A"><Name>TEST Ostrava stanice</Name></StopPlace><StopPlace id="PLACE-B"><Name>TEST Ostrava centrum</Name></StopPlace></stopPlaces></SiteFrame>
<ServiceFrame><lines><Line id="L-{code}"><PublicCode>{code}</PublicCode><TransportMode>{mode}</TransportMode><OperatorRef ref="OP"/></Line></lines><scheduledStopPoints><ScheduledStopPoint id="SSP-A"/><ScheduledStopPoint id="SSP-B"/></scheduledStopPoints><stopAssignments><PassengerStopAssignment id="AS-A"><ScheduledStopPointRef ref="SSP-A"/><StopPlaceRef ref="PLACE-A"/></PassengerStopAssignment><PassengerStopAssignment id="AS-B"><ScheduledStopPointRef ref="SSP-B"/><StopPlaceRef ref="PLACE-B"/></PassengerStopAssignment></stopAssignments><journeyPatterns><ServiceJourneyPattern id="PATTERN"><LineRef ref="L-{code}"/><pointsInSequence><StopPointInJourneyPattern id="POINT-A"><ScheduledStopPointRef ref="SSP-A"/><ForBoarding>{pickup}</ForBoarding></StopPointInJourneyPattern><StopPointInJourneyPattern id="POINT-B"><ScheduledStopPointRef ref="SSP-B"/></StopPointInJourneyPattern></pointsInSequence></ServiceJourneyPattern></journeyPatterns></ServiceFrame>
<ServiceCalendarFrame><ServiceCalendar><operatingPeriods><UicOperatingPeriod id="PERIOD"><FromDate>2026-10-26</FromDate><ToDate>2026-10-28</ToDate><ValidDayBits>{bits}</ValidDayBits></UicOperatingPeriod></operatingPeriods><dayTypeAssignments><DayTypeAssignment id="ASSIGN"><DayTypeRef ref="DAYS"/><OperatingPeriodRef ref="PERIOD"/></DayTypeAssignment></dayTypeAssignments></ServiceCalendar></ServiceCalendarFrame>
<TimetableFrame><vehicleJourneys><ServiceJourney id="J-{code}" version="1"><Name>{code}</Name><JourneyPatternRef ref="PATTERN"/><dayTypes><DayTypeRef ref="DAYS"/></dayTypes><passingTimes><TimetabledPassingTime><StopPointInJourneyPatternRef ref="POINT-A"/><DepartureTime>{departure}</DepartureTime></TimetabledPassingTime><TimetabledPassingTime><StopPointInJourneyPatternRef ref="POINT-B"/><ArrivalTime>{arrival}</ArrivalTime></TimetabledPassingTime></passingTimes></ServiceJourney></vehicleJourneys></TimetableFrame>
</frames></CompositeFrame></dataObjects></PublicationDelivery>''')

def network_fixture():
    rail = RailMessages();rail.add(train());rail_network,_=rail.export(DAY,'2026-10-28')
    city = OstravaNetex(DAY,'2026-10-28');city.add(ostrava());city.add(ostrava('bus',code='TESTB',departure='09:45:00',arrival='10:00:00'));city.add(ostrava('trolleyBus',code='TESTT',departure='10:00:00',arrival='10:15:00'))
    city_network,_=city.export()
    pid=blank(DAY,'2026-10-28','synthetic-pid')
    pid['stops']=[{'id':'TEST-HOTEL','name':'TEST Hotel','lat':0,'lon':0,'type':0,'parent':'','group':'TEST-HOTEL','zone':'','platform':''},{'id':'TEST-PRAHA','name':'TEST Praha stanice','lat':0,'lon':0,'type':0,'parent':'','group':'TEST-PRAHA','zone':'','platform':''}]
    add_service(pid,'TEST-PID',['TEST-PID','TEST12',0],'TEST Praha stanice','',[[0,18000,18000,0,0,1,1],[1,20400,20400,0,0,2,1]],pid['active'])
    finalize(pid)
    links={'schema':1,'links':[{'from':'TEST-PRAHA','to':'czrail:CZ:A','seconds':300,'bidirectional':True,'verified':True,'verifiedAt':'2026-10-01T00:00:00Z','evidence':'https://example.invalid/synthetic-fixture'},{'from':'czrail:CZ:C','to':'ostrava:SSP-A','seconds':300,'bidirectional':True,'verified':True,'verifiedAt':'2026-10-01T00:00:00Z','evidence':'https://example.invalid/synthetic-fixture'}]}
    network=compose([('pid',pid),('rail',rail_network),('ostrava',city_network)],DAY,'2026-10-28',links)
    network['syntheticFixture']=True
    return network

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);args=parser.parse_args()
    Path(args.out).write_text(json.dumps(network_fixture(),ensure_ascii=False),encoding='utf-8')
