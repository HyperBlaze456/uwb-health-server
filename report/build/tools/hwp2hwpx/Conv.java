import kr.dogfoot.hwplib.object.HWPFile;
import kr.dogfoot.hwplib.reader.HWPReader;
import kr.dogfoot.hwp2hwpx.Hwp2Hwpx;
import kr.dogfoot.hwpxlib.object.HWPXFile;
import kr.dogfoot.hwpxlib.writer.HWPXWriter;
public class Conv {
    public static void main(String[] a) throws Exception {
        HWPFile from = HWPReader.fromFile(a[0]);
        HWPXFile to = Hwp2Hwpx.toHWPX(from);
        HWPXWriter.toFilepath(to, a[1]);
        System.out.println("ok");
    }
}
